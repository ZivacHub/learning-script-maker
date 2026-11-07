"""Builds a small MADE-UP study folder ("Computer networks, oral exam") for the showcase screenshots.

Nothing here comes from real course material: the text is invented for this demo, so screenshots of the app running
on it contain no private notes. The layout copies how a real folder looks (question lists in `Pitanja/`, lecture
text in `Materijali/`, one sub-folder per unit named "1. Title").

Run: uv run python docs/showcase/make_sample_material.py <target-folder>
"""

from __future__ import annotations

# ruff: noqa: E501  (the sample lecture sentences are long on purpose)
import sys
from pathlib import Path

from docx import Document

UNITS = [
    (
        "1. Osnove mreža",
        [
            ("Što je lokalna mreža?", True),
            ("Koja je razlika između preklopnika (switch) i usmjerivača (router)?", True),
            ("Što je prostrana mreža?", True),
            ("Navedite tri vrste mrežnih topologija.", False),
        ],
        [
            "Lokalna mreža (LAN) je mreža koja povezuje računala na malom području, na primjer u kući ili u učionici.",
            "Preklopnik (switch) spaja uređaje unutar iste mreže i prosljeđuje okvire samo onom uređaju kojem su namijenjeni.",
            "Usmjerivač (router) spaja različite mreže i odlučuje kojim putem će paket stići do odredišta.",
            "Širokopojasna mreža (WAN) povezuje lokalne mreže na velikim udaljenostima, a najveća takva mreža je internet.",
            "Mreže se u praksi najčešće grade po zvjezdastoj topologiji jer kvar jednog kabela ne ruši cijelu mrežu.",
        ],
    ),
    (
        "2. IP adrese i DNS",
        [
            ("Što je IP adresa?", True),
            ("Čemu služi podmrežna maska?", True),
            ("Što radi poslužitelj imena domena?", True),
            ("Kako se uređajima automatski dodjeljuju adrese?", True),
            ("Objasnite razliku između IPv4 i IPv6 adrese.", False),
        ],
        [
            "IP adresa je jedinstvena oznaka uređaja u mreži pomoću koje se paketi isporučuju na pravo odredište.",
            "Podmrežna maska određuje koji dio IP adrese označava mrežu, a koji dio označava pojedini uređaj u njoj.",
            "DNS poslužitelj pretvara imena stranica, na primjer example.org, u IP adrese koje računala mogu koristiti.",
            "DHCP poslužitelj automatski dodjeljuje IP adrese uređajima koji se spoje na mrežu, pa ih nitko ne mora upisivati ručno.",
            "Svaki uređaj u istoj lokalnoj mreži mora imati drukčiju IP adresu, inače dolazi do sukoba adresa.",
        ],
    ),
    (
        "3. Sigurnost mreže",
        [
            ("Što je vatrozid?", True),
            ("Čemu služi virtualna privatna mreža?", True),
            ("Što je phishing?", True),
            ("Zašto je važna šifrirana veza u pregledniku?", True),
        ],
        [
            "Vatrozid (firewall) je program ili uređaj koji propušta ili blokira mrežni promet prema unaprijed zadanim pravilima.",
            "VPN stvara šifrirani tunel između uređaja i udaljene mreže, pa ostali na putu ne mogu pročitati promet.",
            "Phishing je prijevara u kojoj napadač lažnom porukom ili stranicom pokušava izvući lozinku ili podatke o kartici.",
            "HTTPS šifrira vezu između preglednika i stranice, pa lozinke i drugi podaci ne putuju mrežom kao običan tekst.",
            "Dobra lozinka je duga, jedinstvena za svaku uslugu i nikad se ne dijeli s drugima.",
        ],
    ),
]


def build(target: Path) -> None:
    for title, questions, paragraphs in UNITS:
        questions_dir = target / "Pitanja" / title
        material_dir = target / "Materijali" / title
        questions_dir.mkdir(parents=True, exist_ok=True)
        material_dir.mkdir(parents=True, exist_ok=True)

        doc = Document()
        for number, (text, _) in enumerate(questions, start=1):
            doc.add_paragraph(f"{number}. {text}")
        doc.save(questions_dir / "pitanja.docx")

        notes = Document()
        notes.add_heading(title[3:], level=1)
        for paragraph in paragraphs:
            notes.add_paragraph(paragraph)
        notes.save(material_dir / "predavanje.docx")


if __name__ == "__main__":
    out = Path(sys.argv[1])
    build(out)
    print(f"made {out}")
