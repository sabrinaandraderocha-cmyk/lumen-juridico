import os
import re
from collections import Counter
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Leitura de arquivos
from pypdf import PdfReader
from docx import Document

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-change-me")

# =========================
# Upload config
# =========================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "instance", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Limite de tamanho (ex.: 8 MB)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

ALLOWED_EXTS = {".pdf", ".docx"}  # Word = .docx

STOPWORDS_PT = {
    "a","o","os","as","um","uma","uns","umas","de","do","da","dos","das","em","no","na","nos","nas",
    "por","para","com","sem","sobre","entre","e","ou","que","se","ao","aos","à","às","como","mais",
    "menos","já","não","sim","ser","foi","é","são","era","sendo","ter","tem","têm","haver","há",
    "art","artigo","lei","decreto","resolução","acórdão","relator","relatora","turma","câmara",
    "tribunal","stj","stf","tj","trf","ministro","ministra","voto","decisão","processo","recurso"
}

def normalize(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text

def split_sentences(text: str):
    parts = re.split(r"(?<=[\.\?!])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]

def extract_block(text: str, start_patterns, stop_patterns, max_chars=4000):
    lower = text.lower()
    start_idx = None
    for pat in start_patterns:
        m = re.search(pat, lower, flags=re.I | re.M)
        if m:
            start_idx = m.start()
            break
    if start_idx is None:
        return ""

    tail = text[start_idx:]
    tail_lower = lower[start_idx:]

    stop_idx = None
    for sp in stop_patterns:
        m = re.search(sp, tail_lower, flags=re.I | re.M)
        if m and m.start() > 0:
            stop_idx = m.start()
            break

    block = tail[:stop_idx] if stop_idx else tail
    return block.strip()[:max_chars].strip()

def pick_keywords(text: str, k=5):
    tokens = re.findall(r"[A-Za-zÀ-ÿ]{3,}", text.lower())
    tokens = [t for t in tokens if t not in STOPWORDS_PT]
    if not tokens:
        return []
    counts = Counter(tokens)
    return [w for w, _ in counts.most_common(k)]

def extract_legal_citations(text: str, limit=8):
    patterns = [
        r"\bart\.?\s*\d+[a-zA-Zº°]*\b(?:\s*,\s*§\s*\d+º?)?(?:\s*,\s*inc\.\s*[ivxlcdm]+)?",
        r"\blei\s*n[ºo]\s*\d[\d\.\-]*\s*(?:/|\s*de\s*)\s*\d{2,4}\b",
        r"\bdecreto-lei\s*n[ºo]\s*\d[\d\.\-]*\b",
        r"\bconstitui[cç][aã]o\s*federal\b|\bCF/88\b|\bCF\b",
        r"\bCPC\b|\bCPP\b|\bCP\b|\bCLT\b|\bCDC\b"
    ]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.I):
            snippet = re.sub(r"\s+", " ", m.group(0).strip())
            if snippet and snippet.lower() not in [f.lower() for f in found]:
                found.append(snippet)
            if len(found) >= limit:
                return found
    return found

def build_output(text: str):
    text = normalize(text)

    ementa = extract_block(
        text,
        start_patterns=[r"^ementa\b", r"\bementa\b"],
        stop_patterns=[r"^ac[oó]rd[aã]o\b", r"^relat[oó]rio\b", r"^voto\b", r"^decis[aã]o\b"]
    ) or text[:900].strip()

    question = ""
    for s in split_sentences(text):
        if s.endswith("?") and len(s) <= 240:
            question = s
            break

    if not question:
        kws = pick_keywords(ementa, k=4)
        question = (
            f"Qual é a controvérsia principal envolvendo {', '.join(kws[:3])}?"
            if kws else
            "Qual é a controvérsia principal do caso?"
        )

    tese = extract_block(
        text,
        start_patterns=[r"\btese\b", r"\bconclus[aã]o\b", r"\bdecide-se\b"],
        stop_patterns=[r"^fundamenta[cç][aã]o\b", r"^relat[oó]rio\b", r"^voto\b", r"^dispositivo\b"],
        max_chars=900
    )
    if not tese:
        ementa_sents = split_sentences(ementa)
        tese = " ".join(ementa_sents[:2]) if ementa_sents else ementa[:350]

    fundamentos = extract_legal_citations(text, limit=8) or [
        "(nenhuma referência legal detectada automaticamente — revise manualmente)"
    ]

    low = text.lower()
    hints = []
    if any(w in low for w in ["concurso", "prova objetiva", "questão", "exame da ordem", "oab"]):
        hints.append("Estudo/Prova: útil para questões de jurisprudência e fundamentos.")
    if any(w in low for w in ["petição", "inicial", "contestação", "recurso", "agravo", "apelação", "habeas corpus", "mandado de segurança"]):
        hints.append("Prática: pode virar argumento em peça/recurso com boa chance de pertinência.")
    if not hints:
        hints.append("Use como base para: (i) montar argumento; (ii) comparar casos semelhantes; (iii) revisar requisitos e exceções.")

    return {
        "pergunta": question.strip(),
        "tese": tese.strip(),
        "fundamentos": fundamentos,
        "aplicacao": " ".join(hints).strip(),
        "ementa": ementa.strip(),
    }

# =========================
# File helpers
# =========================
def allowed_file(filename: str) -> bool:
    _, ext = os.path.splitext(filename.lower())
    return ext in ALLOWED_EXTS

def extract_text_from_pdf(path: str) -> str:
    reader = PdfReader(path)
    chunks = []
    for page in reader.pages:
        txt = page.extract_text() or ""
        if txt.strip():
            chunks.append(txt)
    return "\n".join(chunks).strip()

def extract_text_from_docx(path: str) -> str:
    doc = Document(path)
    parts = []
    for p in doc.paragraphs:
        if p.text and p.text.strip():
            parts.append(p.text.strip())
    return "\n".join(parts).strip()

def get_text_from_upload(file_storage) -> str:
    filename = secure_filename(file_storage.filename or "")
    if not filename:
        return ""

    if not allowed_file(filename):
        return ""

    _, ext = os.path.splitext(filename.lower())
    save_path = os.path.join(UPLOAD_DIR, f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}")
    file_storage.save(save_path)

    try:
        if ext == ".pdf":
            return extract_text_from_pdf(save_path)
        if ext == ".docx":
            return extract_text_from_docx(save_path)
        return ""
    finally:
        # remove arquivo após extrair texto (privacidade)
        try:
            os.remove(save_path)
        except OSError:
            pass

@app.get("/")
def home():
    return render_template("index.html")

@app.post("/analisar")
def analisar():
    texto = (request.form.get("texto") or "").strip()
    arquivo = request.files.get("arquivo")

    # Se enviou arquivo, tenta extrair texto dele
    if arquivo and arquivo.filename:
        if not allowed_file(arquivo.filename):
            flash("Formato não suportado. Envie PDF ou DOCX (Word).", "error")
            return redirect(url_for("home"))

        extraido = get_text_from_upload(arquivo)
        if not extraido:
            flash("Não foi possível extrair texto do arquivo. Se for PDF escaneado, exporte para PDF pesquisável ou cole o texto manualmente.", "error")
            return redirect(url_for("home"))

        # Se também tiver texto colado, juntamos
        texto = f"{texto}\n\n{extraido}".strip() if texto else extraido

    if not texto:
        flash("Cole um texto ou envie um arquivo (PDF/DOCX) para analisar.", "error")
        return redirect(url_for("home"))

    out = build_output(texto)
    return render_template("resultado.html", out=out, texto=texto, now=datetime.now())

@app.get("/biblioteca")
def biblioteca():
    links = [
        {
            "titulo": "Constituição Federal (publicação original no DOU) — PDF (Planalto)",
            "url": "https://www.planalto.gov.br/ccivil_03/constituicao/DOUconstituicao88.pdf",
            "tipo": "PDF (Planalto)"
        },
        {
            "titulo": "Constituição Federal (texto compilado) — HTML (Planalto)",
            "url": "https://www.planalto.gov.br/ccivil_03/constituicao/constituicaocompilado.htm",
            "tipo": "HTML (Planalto)"
        },
        {
            "titulo": "Código Penal (texto compilado) — HTML (Planalto)",
            "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm",
            "tipo": "HTML (Planalto)"
        },
        {
            "titulo": "Código de Processo Penal (texto compilado) — HTML (Planalto)",
            "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm",
            "tipo": "HTML (Planalto)"
        },
        {
            "titulo": "Código Civil (texto compilado) — HTML (Planalto)",
            "url": "https://www.planalto.gov.br/ccivil_03/leis/2002/l10406compilada.htm",
            "tipo": "HTML (Planalto)"
        },
        {
            "titulo": "Código de Processo Civil (Lei 13.105/2015) — HTML (Planalto)",
            "url": "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm",
            "tipo": "HTML (Planalto)"
        },
        {
            "titulo": "Portal da Legislação (Planalto) — Códigos (atalhos oficiais)",
            "url": "https://www4.planalto.gov.br/legislacao/portal-legis/legislacao-1/codigos-1",
            "tipo": "Portal (Planalto)"
        },
        {
            "titulo": "Constituição Federal – 136ª Emenda (link que você enviou)",
            "url": "https://share.google/1awiFMmPIeEJG2ICq",
            "tipo": "Link externo"
        },
    ]

    # remove o primeiro e o último link, como você pediu
    if len(links) >= 2:
        links = links[1:-1]

    return render_template("biblioteca.html", links=links)

@app.get("/sobre")
def sobre():
    return render_template("sobre.html")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
