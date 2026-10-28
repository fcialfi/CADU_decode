# CADU_decode

Decodifica i campi fissi dei transfer frame contenuti in un file di CADU (CCSDS TM).

**Autore:** Fabio Cialfi (Exostaff). Software proprietario: vedi [Licenza](#licenza).

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

- numero di CADU;
- SCID trovati (solo dai frame buoni);
- master channel frame counter: primo e ultimo valore, salti e frame mancanti stimati (contatore a 8 bit:
  i salti sono contati modulo 256);
- per ogni **virtual channel**: numero di frame e quota, frame idle (FHP 7FE), frame con inizio di
  pacchetto, frame senza header (FHP 7FF), primo e ultimo VCFC, salti e frame mancanti, frame corretti dal
  Reed-Solomon. I frame non correggibili non sono contati: il loro header (VC compreso) non è affidabile;
- **CLCW**: valori di VC, COP in effect, status e report value, e in quanti frame sono attivi i flag
  No RF available, No bit lock, Lockout, Wait, Retransmit.
- la **CLCW più frequente** e l'elenco dei CADU con una CLCW diversa (numero, offset, VC, MCFC e campi che
  cambiano, per esempio `report 8→14`): sono cambiamenti durante i telecomandi o frame con errori, perché il
  Reed-Solomon non è verificato. Il riepilogo ne elenca 50; `--frames` e `-o` danno tutti i frame.

L'elenco frame per frame si aggiunge con `--frames` (o tutti i frame in CSV con `-o`).

### Pacchetti

I **Space Packet** sono ricostruiti dai campi dati di ogni virtual channel, in ordine di arrivo: un pacchetto
che continua nel frame successivo dello stesso VC viene ricomposto, e il First Header Pointer di ogni frame
controlla che il pacchetto precedente finisca proprio lì. I frame non correggibili sono lasciati fuori; dove
mancano frame di un VC (salto del contatore VCFC) il pacchetto a cavallo è perso (*cut*) e la catena riparte
dal primo header successivo. Il riepilogo dà:

- per **VC**: pacchetti, byte, byte di **idle packet** (APID 2047) e la loro quota, pacchetti persi a cavallo
  dei frame mancanti, risincronizzazioni (pacchetto che non finisce dove dice l'FHP, senza frame mancanti);
- per **APID e VC**: pacchetti, byte, lunghezza, primo e ultimo sequence count e i **salti del sequence
  count**, separati in *senza frame persi* (pacchetti che non sono mai arrivati in questo VC: scartati a bordo
  o mandati su un altro VC) e *ai frame persi* (spiegati dai frame mancanti); un contatore che **torna indietro** (salto
  di oltre metà dei 16384 valori) è contato come *back jump* e non come pacchetti mancanti: è un altro tratto
  di dati, per esempio il dump della memoria dopo il real-time, o un dump fatto a blocchi;
- la **continuità** del contatore è verificata solo per gli APID scelti (default **0 e 100**: time reporting e dati scientifici;
  `--check-apids 15-16,100`, nella finestra il campo *Check the continuity of APIDs*): per gli altri il
  satellite può registrare solo una parte dei report (filtrati o campionati a bordo) e i salti non sono perdite;
- l'elenco dei salti senza frame persi degli APID verificati: APID, VC, contatori prima e dopo, CADU prima e dopo e i VC dove si
  trovano i contatori mancanti (stesso APID su un altro VC).

`--export-packets cartella` scrive i pacchetti di ogni APID in `cartella/apid_NNNN.bin` (uno dopo l'altro, in
ordine di arrivo) e l'elenco di tutti in `packets.csv`; gli idle packet sono lasciati fuori. `--no-packets`
salta la ricostruzione.

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

- in alto la barra blu con il logo Exostaff (sotto: "by F. Cialfi"), nome e versione del programma, la licenza (nell'eseguibile con
  licenza), **About** (versione, autore e licenza; anche F1) ed **Exit**;
- **Reed-Solomon check** e **Exclude uncorrectable frames** in alto; la colonna RS dice ok, corr. n (byte
  corretti) o FAIL; senza il controllo Reed-Solomon le caselle che ne dipendono (Exclude uncorrectable frames,
  Only RS errors) sono tolte e disattivate; con i frame non correggibili esclusi, Only RS errors diventa Only RS-corrected frames (gli unici errori rimasti sono i frame corretti);
- **Open…** carica il file; interleaving, randomizzazione (Auto / Yes / No) e FECF si cambiano in alto e il file
  viene riletto; accanto al nome del file è scritto se i codeblock sono stati letti come randomizzati;
- scheda **Summary**: il riepilogo in forma grafica: riquadri con i numeri principali (CADU, frame senza errori, corretti, non correggibili, FER,
  spacecraft), il grafico degli errori Reed-Solomon lungo il file (byte corretti per frame
  e frame non correggibili in rosso, con i bordi del 5 % in grigio; passando col mouse un tooltip dà il CADU, la barra e i frame non correggibili lì vicino; trascinando o con la rotella si fa lo zoom fino al singolo CADU, clic destro torna al file intero, doppio clic apre il CADU), le tabelle dei virtual channel, dei pacchetti per VC e per APID (in arancione gli APID con salti del sequence count), dei salti senza frame persi, della CLCW
  più frequente e di quelle diverse, e dei frame non correggibili; doppio clic su una riga: apre il CADU;
- scheda **Text report**: lo stesso riepilogo come testo, con **Copy to clipboard**;
- scheda **Frames**: la tabella dei frame, con filtro per virtual channel e caselle che,
  spuntate, mostrano meno frame: **Hide idle frames**, **Only RS errors**, **Only unusual CLCW**; **Go to #**. Selezionando una riga, sotto compare il CADU in forma
  grafica: stato Reed-Solomon, struttura (sync marker, header, data field, OCF, simboli RS con i byte), campi del
  primary header e della CLCW a caselle (bit, binario, valore; in arancione i campi della CLCW diversi dalla più
  frequente), le 5 parole Reed-Solomon, i pacchetti del data field e il dump esadecimale colorato per sezione con
  i byte corretti evidenziati (**Hex dump** per mostrarlo o no; **Copy as text** copia la versione testuale);
- **Export** → *Frames to CSV…* salva tutti i frame; *Packets, one file per APID…* scrive i pacchetti di ogni
  APID in una cartella (come `--export-packets`).

## Eseguibile per Windows

`CADU-Decoder.exe` è la finestra, nella cartella `CADU-Decoder\` insieme alle sue librerie (in `_internal\`): non
serve Python sul PC. Si distribuisce l'intera cartella (ad esempio come zip). Si apre con doppio clic; trascinando un
file di CADU sull'icona lo apre subito. Si chiude con **Exit**, Ctrl+Q o la X della finestra. Ha l'icona e il
logo Exostaff (cartella `assets/`, inclusa dallo spec) e, se è stata creata la chiave pubblica, il controllo della
licenza (sezione sotto): in quel caso va consegnato insieme al `license.key` del cliente.

**Avvio**: appena lanciato l'exe mostra una schermata con il logo Exostaff (`assets/splash.png`: "CADU Decoder,
Starting up", by F. Cialfi), che si chiude quando la finestra è pronta. È la modalità "cartella", come per il Link
Budget Tool: parte in pochi secondi perché non deve decomprimere nulla. In `cadu_gui.spec` si può impostare
`ONEFILE = True` per avere un unico `.exe`, che però a ogni avvio decomprime le librerie (numpy e le altre) in una
cartella temporanea e parte più lentamente, soprattutto con l'antivirus attivo.

- **Da GitHub**: a ogni push su `main` la action *Build* esegue i test e crea la cartella; si scarica dalla pagina
  della run (scheda Actions, artifact *CADU-Decoder*, uno zip con dentro la cartella `CADU-Decoder`). Un tag `v…`
  allega anche lo zip della cartella a una release.
- **In locale**: doppio clic su `build.bat` (crea `.venv` con numpy, cryptography e PyInstaller la prima volta) →
  `dist\CADU-Decoder\CADU-Decoder.exe`; `build.bat zip` crea anche `dist\CADU-Decoder_<versione>.zip` da consegnare
  (dopo averci aggiunto il `license.key` del cliente). A mano: `pip install -r requirements.txt pyinstaller` e
  `pyinstaller cadu_gui.spec`.

### Licenza a scadenza dell'eseguibile

L'eseguibile può essere limitato con un file di licenza che scade in una data stabilita (`licensing.py`, lo stesso
sistema del Satellite Link Budget Tool e dell'HKTM CCSDS Signal Generator). La licenza è un piccolo file
`license.key`, da mettere nella cartella `CADU-Decoder`, accanto a `CADU-Decoder.exe`, con il nome del cliente e la data di scadenza,
firmato con una chiave privata che ha solo chi emette le licenze. L'eseguibile contiene solo la chiave pubblica: può
verificare una licenza ma non crearne una, e modificare il file (ad esempio la data) la invalida. Senza una licenza
valida il programma si ferma all'avvio con una spiegazione; la licenza valida è indicata nella barra in alto della
finestra e in About, in arancione negli ultimi 30 giorni. Mettere indietro l'orologio del PC non serve: la data più
recente vista è ricordata nel profilo dell'utente. Lanciato da sorgente (`python cadu_gui.py`) il programma non
chiede mai la licenza.

1. **Una volta sola:** crea la coppia di chiavi (serve `pip install cryptography`):
   ```
   python license_tool.py keygen --private-key C:\secure\cadu_decoder_license_private_key.pem
   git add assets\license_public_key.pem
   git commit -m "Add licence public key"
   ```
   - La **chiave privata** va dove indichi: tienila al sicuro e fuori dal repository. Chi la possiede può emettere
     licenze; se va persa servono una nuova coppia di chiavi e un nuovo eseguibile.
   - La **chiave pubblica** va in `assets/license_public_key.pem` e va nel repository: ogni eseguibile costruito da
     quel momento (in locale o dalla action *Build*) verifica la licenza. Gli eseguibili costruiti senza questo file
     non hanno il controllo. Se il file esiste già, non rifare le chiavi: le licenze già emesse non varrebbero più.
2. **Per ogni cliente:** emetti una licenza e inviala insieme all'eseguibile:
   ```
   python license_tool.py issue --private-key C:\secure\cadu_decoder_license_private_key.pem --customer "Nome cliente" --expires 2027-12-31 -o license.key
   ```
3. **Verifica facoltativa:** `python license_tool.py verify license.key`

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

## Licenza

Copyright (c) 2026 Fabio Cialfi (Exostaff). Software proprietario, tutti i diritti riservati: vedi
[LICENSE](LICENSE). L'uso dell'eseguibile richiede una licenza cliente valida (`license.key`).
