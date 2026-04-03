"""LexArdor Reasoning Benchmark — runs 60 questions through the RAG pipeline.

Sends each question, captures the answer, and writes results to markdown.
Run twice: once with reasoning model (DeepSeek-R1 32B), once with fast model (Qwen 9B).

Usage:
    python tests/reasoning_benchmark.py --model deepseek --output tests/reasoning_deepseek.md
    python tests/reasoning_benchmark.py --model fast --output tests/reasoning_qwen9b.md
"""
import argparse
import json
import sys
import time
import requests

BASE = "http://localhost:8080"

QUESTIONS = {
    "1) Pitanja koja bi mogli da postave pripravnici jer ne znaju zakon dovoljno": [
        "Koja je razlika između prekršaja, krivičnog dela i privrednog prestupa u srpskom pravu?",
        "Kako da utvrdim koji sud je stvarno i mesno nadležan u građanskom sporu?",
        "Kada tužba može da bude odbačena, a kada odbijena? Objasni razliku.",
        "Koja je razlika između zastarelosti potraživanja i prekluzivnog roka?",
        "Kako se pravilno računa rok za žalbu ako je rešenje primljeno u petak?",
        "Šta znači da je neka presuda pravnosnažna, a šta da je izvršna?",
        "Ko može biti stranka u postupku i šta znači procesna sposobnost?",
        "Kada se koristi prigovor, kada žalba, a kada revizija?",
        "Koja je razlika između ugovora koji je ništav i ugovora koji je rušljiv?",
        "Kako se dokazuje postojanje usmenog ugovora ako ne postoji pisani trag?",
    ],
    "2) Pitanja koja se tiču sudske prakse": [
        "Kako sudovi u Srbiji najčešće ocenjuju dokaznu snagu Viber, WhatsApp i SMS poruka?",
        "Kakva je sudska praksa u vezi sa naknadom nematerijalne štete zbog povrede časti i ugleda na internetu?",
        "Kako sudovi tumače postojanje radnog odnosa kada je formalno zaključen ugovor o privremenim i povremenim poslovima?",
        "Da li sudska praksa priznaje usmeni zajam između fizičkih lica i koji dokazi su najvažniji?",
        "Kako sudovi u praksi odlučuju o poveravanju deteta kada oba roditelja traže samostalno vršenje roditeljskog prava?",
        "Kakav je pristup sudova kada poslodavac daje otkaz zbog povrede radne discipline, a procedura nije potpuno ispoštovana?",
        "Kako sudovi odlučuju u sporovima zbog smetanja državine između komšija oko prolaza, ograde ili parking mesta?",
        "Da li sudska praksa prihvata da e-mail prepiska zameni formalni pisani aneks ugovora?",
        "Kako se u praksi utvrđuje doprinos oštećenog kod saobraćajnih nezgoda radi umanjenja naknade štete?",
        "Kakva je sudska praksa u vezi sa kamatom i valutnom klauzulom u privatnim ugovorima među građanima?",
    ],
    "3) Pitanja koja se tiču advokatske prakse": [
        "Klijent tvrdi da je pozajmio novac prijatelju bez ugovora, ali ima poruke i svedoka. Kako bi advokat strukturisao tužbeni zahtev i dokazni predlog?",
        "Kako advokat procenjuje da li je bolje pokrenuti parnicu, izvršni postupak ili prvo poslati opomenu pred tužbu?",
        "Klijent želi da tuži poslodavca zbog nezakonitog otkaza. Koju dokumentaciju advokat prvo traži i zašto?",
        "Kako advokat procenjuje da li postoji osnov za privremenu meru pre pokretanja glavnog postupka?",
        "Na šta advokat mora da obrati pažnju kada sastavlja ugovor o zajmu između fizičkih lica da bi bio lakše izvršiv?",
        "Klijent je primio tužbu i tvrdi da 'nije ništa potpisao'. Kako advokat proverava procesnu strategiju odbrane?",
        "Kako advokat savetuje klijenta kada postoje i krivičnopravni i građanskopravni elementi istog događaja?",
        "Koje su tipične greške koje advokati izbegavaju prilikom sastavljanja žalbe u građanskom postupku?",
        "Kako advokat procenjuje da li da predloži veštačenje ili da se osloni na isprave i svedočenja?",
        "Klijent želi 'da tuži državu'. Kako advokat prvo razgraničava protiv koga se konkretno vodi postupak i po kom pravnom osnovu?",
    ],
    "4) Pitanja koja se tiču kompleksnih kombinovanih slučajeva": [
        "Radnik je formalno angažovan preko agencije, radio je kod korisnika rada, povredio se na radu, a poslodavac tvrdi da nije odgovoran. Koji sve pravni režimi mogu biti relevantni i kako bi se postavila pravna analiza?",
        "Supružnici se razvode, vode spor oko starateljstva, dele imovinu, a istovremeno postoji sumnja da je jedan supružnik prenosio novac na treća lica da izbegne deobu. Koji postupci i pravni instrumenti dolaze u obzir?",
        "Investitor prodaje stan koji nije potpuno legalizovan, kupac je isplatio većinu cene, banka odbija kredit, a u međuvremenu se pojavljuje i spor oko hipoteke. Kako pristupiti slučaju?",
        "Direktor privrednog društva zaključi štetan ugovor sa povezanim licem, firma ode u blokadu, a manjinski član društva traži zaštitu. Koje oblasti prava se prepliću i koja pitanja prvo treba rešiti?",
        "Lice objavi uvredljive tvrdnje na društvenim mrežama, medij to prenese, poslodavac otpusti zaposlenog koji je reagovao, a zatim krene i krivična prijava. Kako razdvojiti građansku, radnu, medijsku i eventualnu krivičnu dimenziju?",
        "Naslednik uđe u posed nepokretnosti, ali se pojavi lice koje tvrdi da je steklo pravo održajem, dok treće lice ima neuknjižen ugovor o kupoprodaji. Kako bi AI trebalo da mapira pravne prioritete?",
        "Firma angažuje frilensera, ali mu određuje radno vreme, sredstva rada i nadzor. Istovremeno postoji spor oko autorskih prava na softveru koji je napravio. Kako analizirati odnos radnog, obligacionog i autorskog prava?",
        "Bračni partner podigne kredit, drugi partner tvrdi da nije znao za zaduženje, nekretnina je kupljena tokom braka, a banka pokreće izvršenje. Koja pravna pitanja ovde nastaju?",
        "Maloletnik izazove saobraćajnu nezgodu vozeći vozilo roditelja bez dozvole, osiguranje osporava isplatu, a oštećeni traži naknadu od više lica. Ko može odgovarati i po kom osnovu?",
        "Zakupac poslovnog prostora prestane da plaća zakupninu, ali tvrdi da prostor nije bio podoban za ugovorenu namenu, dok zakupodavac aktivira menicu i pokreće izvršenje. Kako kombinovati obligacionopravnu i izvršnu analizu?",
    ],
    "5) Pitanja koja bi postavio običan građanin": [
        "Dao sam čoveku pare na reč i sad me vrti već godinu dana, šta ja realno mogu?",
        "Komšija je pomerio ogradu i sad ispada da je moje njegovo, kome ja to prijavljujem?",
        "Poslodavac me tera da potpišem nešto retroaktivno, a meni to smrdi. Smem li da odbijem?",
        "Bivša žena neće da mi da dete preko dogovora nego stalno izmišlja nešto, koja su moja prava?",
        "Kupio sam auto, a posle se ispostavilo da ima neki problem sa papirima. Da li mogu da vratim auto i pare?",
        "Izvršitelj mi je seo na platu, ali meni niko ništa nije objasnio kako je do toga došlo. Kako da proverim šta se dešava?",
        "Gazda stana neće da mi vrati depozit i priča da je sve moje krivica. Kako se to rešava?",
        "Neko me je javno pljuvao po Fejsbuku i ljudi me sad gledaju kao budalu. Mogu li da ga tužim?",
        "Firma me drži prijavljenog na minimalac, a ostatak daje na ruke. Šta meni to pravi kao problem kasnije?",
        "Umro mi je otac, a rodbina je odmah počela da deli stvari kao da je sve njihovo. Koji je prvi pravni korak koji treba da uradim?",
    ],
    "6) Pitanja koja testiraju AI sposobnost razumevanja srpskog prava": [
        "Navedi koji zakon i koja grana prava su primarno relevantni za spor oko otkaza zaposlenom zbog objave na društvenim mrežama, ali bez izmišljanja činjenica koje nisu date.",
        "Ako korisnik pita: 'Da li mogu da tužim jer me je prevario?', postavi listu potpitanja koja su nužna da bi se razlikovala krivična prevara od građanskopravnog spora.",
        "Objasni razliku između pravnosnažnosti, izvršnosti i konačnosti, uz napomenu u kojim vrstama postupaka se ti pojmovi javljaju.",
        "Korisnik navodi da 'ima ugovor', ali ne zna da li je overen, potpisan od obe strane ili samo poslat mejlom. Kako AI treba da vodi pravnu analizu bez preuranjenog zaključka?",
        "Daj odgovor na pitanje o zastarelosti, ali tako da prvo objasniš da rok zavisi od vrste potraživanja i pravnog osnova, umesto da daješ jedan univerzalan rok.",
        "U slučaju porodičnog nasilja, koje korake AI treba da prioritizuje: hitna zaštita, prijava, porodičnopravna zaštita, krivičnopravni aspekt ili parnični postupak? Objasni redosled razmišljanja.",
        "Ako korisnik pomeša vlasništvo, državinu i korišćenje stvari, kako AI treba da objasni razliku bez previše stručnog jezika, ali pravno tačno?",
        "Kada korisnik pita za 'tužbu', kako AI treba da proceni da li je zapravo potrebna žalba, prigovor, zahtev za zaštitu prava, prijava inspekciji ili neki drugi pravni lek?",
        "Daj primer situacije u kojoj AI mora da upozori korisnika da odgovor zavisi i od podzakonskih akata, sudske prakse ili posebnog postupka, a ne samo od jednog zakona.",
        "Ako korisnik traži 'tačan član zakona', kako AI treba da postupi kada nije siguran da li raspolaže ažurnom verzijom propisa?",
    ],
}


def run_query(question: str, mode: str = "strict", timeout: int = 300) -> dict:
    """Send a question to LexArdor and return the result."""
    try:
        r = requests.post(f"{BASE}/api/query", json={
            "query": question,
            "answer_mode": mode,
            "top_k": 8,
        }, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            return {
                "answer": data.get("answer", ""),
                "confidence": data.get("confidence", {}),
                "sources": len(data.get("sources", [])),
                "model": data.get("model_used", ""),
                "time_ms": data.get("response_time_ms", 0),
                "citations": data.get("citations", {}),
            }
        else:
            return {"answer": f"ERROR: {r.status_code} — {r.text[:200]}", "error": True}
    except Exception as e:
        return {"answer": f"ERROR: {e}", "error": True}


def generate_report(model_name: str, output_path: str):
    """Run all 60 questions and write results to markdown."""
    print(f"{'='*70}")
    print(f"  LexArdor Reasoning Benchmark — {model_name}")
    print(f"  60 questions across 6 categories")
    print(f"{'='*70}")

    total = 0
    errors = 0
    total_time = 0
    results_by_category = {}

    for category, questions in QUESTIONS.items():
        print(f"\n─── {category} ───")
        results_by_category[category] = []

        for i, q in enumerate(questions, 1):
            total += 1
            print(f"  [{i}/10] {q[:60]}...", end=" ", flush=True)
            t0 = time.time()
            result = run_query(q)
            elapsed = time.time() - t0

            if result.get("error"):
                errors += 1
                print(f"ERROR ({elapsed:.1f}s)")
            else:
                total_time += elapsed
                ans_len = len(result["answer"])
                conf = result["confidence"]
                conf_level = conf.get("level", conf) if isinstance(conf, dict) else conf
                print(f"OK ({elapsed:.1f}s, {ans_len} chars, conf={conf_level})")

            results_by_category[category].append({
                "question": q,
                "result": result,
                "elapsed": elapsed,
            })

    # Write markdown report
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# LexArdor Reasoning Test — {model_name}\n\n")
        f.write(f"**Model:** {model_name}\n")
        f.write(f"**Datum:** 2026-03-31\n")
        f.write(f"**Ukupno pitanja:** {total}\n")
        f.write(f"**Greške:** {errors}\n")
        f.write(f"**Prosečno vreme odgovora:** {total_time/max(total-errors,1):.1f}s\n\n")
        f.write("---\n\n")

        for category, items in results_by_category.items():
            f.write(f"# **{category}**\n\n")

            for i, item in enumerate(items, 1):
                q = item["question"]
                r = item["result"]
                answer = r.get("answer", "ERROR")
                conf = r.get("confidence", {})
                conf_level = conf.get("level", conf) if isinstance(conf, dict) else conf
                sources = r.get("sources", 0)
                elapsed = item["elapsed"]

                f.write(f"**{i}. {q}**\n\n")
                f.write(f"*Pouzdanost: {conf_level} | Izvori: {sources} | Vreme: {elapsed:.1f}s*\n\n")
                f.write(f"{answer}\n\n")
                f.write("---\n\n")

    print(f"\n{'='*70}")
    print(f"  DONE: {total-errors}/{total} answered, {errors} errors")
    print(f"  Avg response time: {total_time/max(total-errors,1):.1f}s")
    print(f"  Report: {output_path}")
    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="current", help="Model label for the report header")
    parser.add_argument("--output", default="tests/reasoning_results.md", help="Output file path")
    args = parser.parse_args()

    # Verify server is up
    try:
        r = requests.get(f"{BASE}/api/health", timeout=5)
        health = r.json()
        if not health.get("ollama_available"):
            print("ERROR: LLM server not available")
            sys.exit(1)
        print(f"Server OK — corpus: {health['corpus_stats'].get('total_articles', 0)} articles")
    except Exception as e:
        print(f"ERROR: Cannot reach server: {e}")
        sys.exit(1)

    generate_report(args.model, args.output)


if __name__ == "__main__":
    main()
