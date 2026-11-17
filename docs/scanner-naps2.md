# Napojení scanneru přes NAPS2

Funkce „Naskenovat“ v dialogu *Nahrát doklad* používá přímé napojení na
[NAPS2](https://www.naps2.com/) pomocí protokolu eSCL. Vyžaduje to nothing na
backendu – prohlížeč mluví rovnou na stolní aplikaci NAPS2 běžící na
prohlížečovém stroji (`http://localhost:9801–9850`).

## Nastavení

1. Nainstalujte a spusťte NAPS2 na stroji, kde se aplikace otevírá (skener
   musí být připojený tam).
2. V NAPS2 otevřete úpravy profilu a zapněte **sdílení** (Share) – NAPS2 pak
   naslouchá na HTTP portech 9801–9850 (HTTPS 9901–9950). Sdílení funguje,
   jen pokud je aplikace NAPS2 spuštěná.
3. Pro přístup z prohlížeče musí NAPS2 posílat CORS hlavičky. Ve
   `config.xml` (v adresáři nastavení NAPS2 – Windows `%AppData%\NAPS2`,
   Linux `~/.config/NAPS2`) nastavte:

   ```xml
   <EsclSecurityPolicy>ServerAllowAnyOrigin</EsclSecurityPolicy>
   ```

   (nebo číselně `64`). Bez tohoto nastavení prohlížeč odpovědi scanneru
   neuvidí a aplikace spadne zpět na skenování kamerou.

## Řešení problémů

- **„Skenování selhalo … (ScanJobs failed with 400)“** – NAPS2 na sdíleném
  profilu odmítne vytvořit skenovací úlohu. Příčina je v NAPS2: server
  kontroluje jmenný prostor skenovacích nastavení přesně a ve své
  konstantě má datum oddělené lomítky
  (`http://schemas.hp.com/imaging/escl/2011/05/03`), kdežto specifikace eSCL
  i ostatní implementace používají pomlčky
  (`http://schemas.hp.com/imaging/escl/2011-05-03`). Na NAPS2 proto narazí každý
  „specifikaci čtecí“ klient – viz [cyanfish/naps2#975][issue] a
  [pull request #976][pr].
  Opačný tvar ale NAPS2 přijímá také (je to jeho vlastní), takže naše
  aplikace při 400 automaticky zkusí i lomítkový zápis a sken proběhne
  i na nespravené NAPS2. Ověřeno na NAPS2 8.6.0 i na aktuálním `master`.
  Ladicí dotaz proti sdílenému profilu (GET `ScannerCapabilities` musí
  vracet 200):

  ```bash
  curl -i -X POST http://localhost:9801/eSCL/ScanJobs \
    -H "Content-Type: text/xml" \
    --data '<?xml version="1.0"?><scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011-05-03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"><pwg:Version>2.6</pwg:Version><scan:XResolution>150</scan:XResolution><scan:YResolution>150</scan:YResolution><scan:ColorMode>BlackAndWhite1</scan:ColorMode><pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat></scan:ScanSettings>'
  ```

- **Lokální build NAPS2 s patchem** – pokud chcete mít server opravený
  (např. aby sdílený profil fungoval i z jiných klientů), spusťte

  ```bash
  scripts/naps2-patched-build.sh          # build a spustí patchovanou NAPS2
  scripts/naps2-patched-build.sh --stop   # vrátí zpět balíček z apt
  ```

  Skript pracuje s kopií v `../naps2`, ničí jen svůj build, neverzí do
  nastavení v `~/.config/naps2` (profily ani sdílení zůstávají).

[issue]: https://github.com/cyanfish/naps2/issues/975
[pr]: https://github.com/cyanfish/naps2/pull/976

## Chování v aplikaci

- Po kliknutí na „Naskenovat“ aplikace probehne porty 9801–9850. Pokud najde
  sdílené profily, zobrazí jejich výběr; sken běží na 150 dpi, černobíle, v
  ploše A4 a **všechny stránky se spojí do jednoho PDF dokumentu**.
- Pokud NAPS2 nenajde, zůstává skenování kamerou (A4 na 150 dpi, černobíle).
- Zaškrtnutím **„Automaticky oříznout okraje“** (výchozí stav zapnuto) se po
  skenu i po skenu kamerou automaticky oříznou bílé okraje ze všech stran a
  kolem textu zůstane cca **5 mm** místa. Vypnutím checkboxu se soubor pošle
  v plné velikosti A4 bez ořezu.
- Po skenu i po sejmutí z kamery se **ihned zobrazí náhled**documentu
  (aby bylo vidět, co se odešle) – teprve po potvrzení „Nahrát“ se dokument
  pošle do systému.
- Parametry skeneru (jemnější rozlišení, barevný sken, papír z podavače)
  ovlivníte v samotném profilu NAPS2.
