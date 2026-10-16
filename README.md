# CADU_decode

Decodifica i campi fissi dei transfer frame contenuti in un file di CADU (CCSDS TM).

Un CADU (CCSDS 131.0-B) è il sync marker a 32 bit `1ACFFC1D` seguito dal codeblock: il transfer frame TM
(CCSDS 132.0-B) e i simboli di controllo Reed-Solomon (255,223), con il codeblock randomizzato dal
pseudo-randomizer CCSDS. Con interleaving I = 5 il CADU è di 4 + 255·5 = 1279 byte e il transfer frame di
223·5 = 1115 byte.

Per ogni CADU lo script:

- si sincronizza sul sync marker (se tra due CADU c'è altro, salta al marker successivo e lo segnala);
- de-randomizza il codeblock (sequenza h(x) = x⁸ + x⁷ + x⁵ + x³ + 1, che inizia FF 48 0E C0 9A …) se è
  randomizzato. Molti ricevitori salvano i CADU già de-randomizzati: lo script lo riconosce da solo provando
  le due letture sui primi 200 CADU (versione del frame 0 e master channel counter che sale di 1). Letti nel modo
  sbagliato i virtual channel escono con il bit più alto invertito (0→4, 1→5, 7→3) e i contatori a salti;
- legge il **primary header**: versione, SCID, VCID, OCF flag, master e virtual channel frame counter,
  secondary header flag, sync flag, packet order flag, segment length ID, First Header Pointer
  (7FF: nessun header di pacchetto, 7FE: solo idle data);
- se il frame ha l'OCF, legge la **CLCW**: type, version, status, COP in effect, VCID, No RF available,
  No bit lock, Lockout, Wait, Retransmit, FARM-B counter, report value.

Il codice Reed-Solomon è decodificato (sezione sotto); i pacchetti del data field sono mostrati con `--show`.

## Reed-Solomon e FER

Lo script decodifica il codice Reed-Solomon (255,223) CCSDS (`rs_ccsds.py`: campo con x⁸ + x⁷ + x² + x + 1,
radici β^112…β^143 con β = α¹¹, base duale di Berlekamp; algoritmo di Berlekamp-Massey come in libfec). Ogni
codeblock contiene I parole interleaved byte per byte (la parola i è fatta dai byte i, i + I, i + 2I, …); ogni
parola corregge fino a 16 byte errati. Per ogni CADU:

- **ok**: nessun errore;
- **corretto**: errori corretti; i campi del frame sono letti dai byte corretti;
- **non correggibile** (FAIL): più di 16 byte errati in almeno una parola; i campi possono essere sbagliati.

Il riepilogo dà i frame senza errori, corretti (e quanti byte), non correggibili, la **FER** (frame non
correggibili / tutti) e la quota di frame con errori prima della correzione, con l'elenco dei CADU non
correggibili. **Errori lungo il file**: i CADU sono salvati in ordine di arrivo a rate costante, quindi la posizione nel file è
il tempo nel passaggio. Il riepilogo divide il file in 10 parti e per ognuna dà frame corretti, byte corretti
(per frame: segue la qualità del segnale) e non correggibili, e la FER senza il 5 % iniziale e finale. Errori
raggruppati ai bordi vengono dall'acquisizione e dalla perdita del segnale (bassa elevazione, transitori di
aggancio); errori sparsi nel passaggio sono errori del link.

`--exclude-bad` esclude i frame non correggibili dall'analisi (virtual channel, contatori, CLCW) e da tutti gli
elenchi: numeri dei CADU non correggibili, `--frames`, tabella della finestra ed export CSV (restano contati
nella FER);
`--no-rs` salta il controllo. Con numpy installato il controllo è veloce (circa 0,3 s ogni 1000 CADU); senza
numpy funziona ma è molto più lento.

## Riepilogo

Per un file con molti CADU lo script mostra di default un riepilogo:

- numero di CADU, byte fuori dai CADU e quante volte la sincronizzazione si è persa e ritrovata;
- SCID trovati;
- master channel frame counter: primo e ultimo valore, salti e frame mancanti stimati (contatore a 8 bit:
  i salti sono contati modulo 256);
- per ogni **virtual channel**: numero di frame e quota, frame idle (FHP 7FE), frame con inizio di
  pacchetto, frame senza header (FHP 7FF), primo e ultimo VCFC, salti e frame mancanti;
- **CLCW**: valori di VC, COP in effect, status e report value, e in quanti frame sono attivi i flag
  No RF available, No bit lock, Lockout, Wait, Retransmit.
- la **CLCW più frequente** e l'elenco dei CADU con una CLCW diversa (numero, offset, VC, MCFC e campi che
  cambiano, per esempio `report 8→14`): sono cambiamenti durante i telecomandi o frame con errori, perché il
  Reed-Solomon non è verificato. Il riepilogo ne elenca 50; `--frames` e `-o` danno tutti i frame.

L'elenco frame per frame si aggiunge con `--frames` (o tutti i frame in CSV con `-o`).

## Contenuto di un CADU

`--show N` mostra un CADU scelto per numero (lo stesso numero dell'elenco `--frames`; anche `5,8`, `10-12` o
`last`):

- sync marker, stato del codeblock (de-randomizzato) e Reed-Solomon di ogni parola, con gli offset dei byte corretti
  (il contenuto è mostrato corretto);
- ogni campo del primary header e della CLCW con numero di bit, valore binario, decimale ed esadecimale;
- il data field: i pacchetti (Space Packet) che iniziano nel frame, letti dal First Header Pointer, con
  APID, sequence flags, sequence count e lunghezza, e se proseguono nel frame successivo;
- il dump esadecimale del codeblock de-randomizzato, con l'inizio di header, data field, OCF e simboli
  Reed-Solomon segnati (`--no-dump` per toglierlo).

## Finestra

`python cadu_gui.py [file]` apre una finestra (Tkinter, incluso in Python):

- **Reed-Solomon check** e **Exclude uncorrectable frames** in alto; la colonna RS dice ok, corr. n (byte
  corretti) o FAIL;
- **Open…** carica il file; interleaving, randomizzazione (Auto / Yes / No) e FECF si cambiano in alto e il file
  viene riletto; accanto al nome del file è scritto se i codeblock sono stati letti come randomizzati;
- scheda **Summary**: il riepilogo in forma grafica: riquadri con i numeri principali (CADU, FER, frame corretti,
  frame senza errori, spacecraft), il grafico degli errori Reed-Solomon lungo il file (byte corretti per frame
  e frame non correggibili in rosso, con i bordi del 5 % in grigio), le tabelle dei virtual channel, della CLCW
  più frequente e di quelle diverse, e dei frame non correggibili; doppio clic su una riga: apre il CADU;
- scheda **Text report**: lo stesso riepilogo come testo, con **Copy to clipboard**;
- scheda **Frames**: la tabella dei frame, con filtro per virtual channel, frame idle nascondibili,
  **Only RS errors**, **Only unusual CLCW** e **Go to #**. Selezionando una riga, sotto compare il CADU in forma
  grafica: stato Reed-Solomon, struttura (sync marker, header, data field, OCF, simboli RS con i byte), campi del
  primary header e della CLCW a caselle (bit, binario, valore; in arancione i campi della CLCW diversi dalla più
  frequente), le 5 parole Reed-Solomon, i pacchetti del data field e il dump esadecimale colorato per sezione con
  i byte corretti evidenziati (**Hex dump** per mostrarlo o no; **Copy as text** copia la versione testuale);
- **Export CSV…** salva tutti i frame.

## Uso

Richiede Python 3.8 o superiore; numpy (`pip install numpy`) è consigliato per un controllo Reed-Solomon veloce.

```
python cadu_decode.py capture.bin                  # riepilogo
python cadu_decode.py capture.bin --frames         # riepilogo e primi 50 frame
python cadu_decode.py capture.bin --frames 0       # riepilogo e tutti i frame
python cadu_decode.py capture.bin -o frames.csv    # tutti i frame in un file CSV
python cadu_decode.py capture.bin --show 125       # contenuto del CADU numero 125
python cadu_decode.py capture.bin --exclude-bad    # senza i frame non correggibili
python cadu_gui.py capture.bin                     # finestra
python cadu_decode.py capture.bin --interleave 4   # altro interleaving (CADU di 4 + 255·I byte)
python cadu_decode.py capture.bin --fecf           # frame con FECF (2 byte) in coda
python cadu_decode.py capture.bin --randomization no   # forza: codeblock già de-randomizzato (yes: randomizzato)
```

Il file può essere binario oppure testo con i byte in esadecimale (spazi e a capo ignorati).

## Test

```
pip install pytest
pytest
```
