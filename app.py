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

app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB
ALLOWED_EXTS = {".pdf", ".docx"}

# =========================
# Linguagem / stopwords
# =========================
STOPWORDS_PT = {
    "a","o","os","as","um","uma","uns","umas","de","do","da","dos","das","em","no","na","nos","nas",
    "por","para","com","sem","sobre","entre","e","ou","que","se","ao","aos","à","às","como","mais",
    "menos","já","não","sim","ser","foi","é","são","era","sendo","ter","tem","têm","haver","há",
    "art","artigo","lei","decreto","resolução","acórdão","relator","relatora","turma","câmara",
    "tribunal","stj","stf","tj","trf","ministro","ministra","voto","decisão","processo","recurso"
}

# =========================
# Utilidades de texto
# =========================
def normalize(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text

def split_sentences(text: str):
    parts = re.split(r"(?<=[\.\?!])\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]

def clean_noise(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    # heurística leve para reduzir vícios de fala
    s = re.sub(r"\b(né|tipo|assim|daí|toda hora)\b", "", s, flags=re.I)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s

def shorten(s: str, max_chars=260) -> str:
    s = clean_noise(s)
    if len(s) <= max_chars:
        return s
    cut = s.rfind(".", 0, max_chars)
    if cut < 80:
        cut = s.rfind(";", 0, max_chars)
    if cut < 80:
        cut = max_chars
    return s[:cut].rstrip(" .;:-") + "…"

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
    tokens = re.findall(r"[A-Za-zÀ-ÿ]{3,}", (text or "").lower())
    tokens = [t for t in tokens if t not in STOPWORDS_PT]
    counts = Counter(tokens)
    return [w for w, _ in counts.most_common(k)]

def extract_legal_citations(text: str, limit=10):
    patterns = [
        r"\bart\.?\s*\d+[a-zA-Zº°]*\b(?:\s*,\s*§\s*\d+º?)?",
        r"\blei\s*n[ºo]\s*\d[\d\.\-]*",
        r"\bdecreto-lei\s*n[ºo]\s*\d[\d\.\-]*",
        r"\bconstitui[cç][aã]o\s*federal\b|\bCF/88\b|\bCF\b",
        r"\bCPC\b|\bCPP\b|\bCP\b|\bCLT\b|\bCDC\b"
    ]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.I):
            val = re.sub(r"\s+", " ", m.group(0).strip())
            if val and val.lower() not in [f.lower() for f in found]:
                found.append(val)
            if len(found) >= limit:
                return found
    return found

def pick_best_question(text: str) -> str:
    # 1) frases com ? e tamanho razoável
    candidates = [s for s in split_sentences(text) if s.endswith("?") and 15 <= len(s) <= 240]
    if candidates:
        return shorten(candidates[0], 240)

    # 2) tenta achar controvérsia/questão em frases com marcadores
    markers = ["discute-se", "controvérsia", "questão", "trata-se", "cuidam os autos", "pretende"]
    for s in split_sentences(text)[:20]:
        low = s.lower()
        if any(m in low for m in markers) and 30 <= len(s) <= 260:
            return shorten(s, 240)

    # 3) fallback com keywords
    kws = pick_keywords(text, k=3)
    if kws:
        return f"Qual é a controvérsia jurídica central envolvendo {', '.join(kws)}?"
    return "Qual é a controvérsia jurídica central do caso?"

# =========================
# Núcleo da análise
# =========================
def build_output(text: str):
    text = normalize(text)

    ementa = extract_block(
        text,
        [r"\bementa\b"],
        [r"\bac[oó]rd[aã]o\b", r"\brelat[oó]rio\b", r"\bvoto\b"]
    ) or text[:900]

    question = pick_best_question(text)

    tese = extract_block(
        text,
        [r"\btese\b", r"\bconclus[aã]o\b", r"\bdecide-se\b"],
        [r"\bfundamenta[cç][aã]o\b", r"\bdispositivo\b"],
        1400
    ) or " ".join(split_sentences(ementa)[:2])

    # versão curta para tela
    tese_curta = shorten(tese, 520)

    fundamentos = extract_legal_citations(text, limit=10) or [
        "(nenhuma referência legal detectada automaticamente)"
    ]

    # aplicação mais objetiva (sem prometer demais)
    aplicacao = (
        "Use este resultado para: (i) revisar a tese do julgado; "
        "(ii) localizar as normas citadas; (iii) estruturar um fichamento "
        "ou argumento em peça/prova."
    )

    return {
        "pergunta": question,
        "tese": tese,
        "tese_curta": tese_curta,
        "fundamentos": fundamentos,
        "aplicacao": aplicacao,
        "ementa": ementa
    }

# =========================
# Upload helpers
# =========================
def allowed_file(filename):
    return os.path.splitext((filename or "").lower())[1] in ALLOWED_EXTS

def extract_text_from_pdf(path):
    reader = PdfReader(path)
    chunks = []
    for p in reader.pages:
        txt = p.extract_text() or ""
        if txt.strip():
            chunks.append(txt)
    return "\n".join(chunks)

def extract_text_from_docx(path):
    doc = Document(path)
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

def get_text_from_upload(file):
    filename = secure_filename(file.filename or "")
    if not filename:
        return ""

    ext = os.path.splitext(filename)[1].lower()
    path = os.path.join(
        UPLOAD_DIR,
        f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
    )
    file.save(path)

    try:
        if ext == ".pdf":
            return extract_text_from_pdf(path)
        if ext == ".docx":
            return extract_text_from_docx(path)
        return ""
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

# =========================
# Rotas
# =========================
@app.get("/")
def home():
    return render_template("index.html")

@app.post("/analisar")
def analisar():
    texto = (request.form.get("texto") or "").strip()
    arquivo = request.files.get("arquivo")

    if arquivo and arquivo.filename:
        if not allowed_file(arquivo.filename):
            flash("Envie apenas PDF ou DOCX.", "error")
            return redirect(url_for("home"))

        extraido = get_text_from_upload(arquivo).strip()
        if not extraido:
            flash(
                "Não foi possível extrair texto do arquivo. "
                "Se for PDF escaneado (imagem), exporte para PDF pesquisável ou cole o texto manualmente.",
                "error"
            )
            return redirect(url_for("home"))

        texto = f"{texto}\n\n{extraido}".strip() if texto else extraido

    if not texto.strip():
        flash("Cole um texto ou envie um arquivo para análise.", "error")
        return redirect(url_for("home"))

    out = build_output(texto)
    return render_template("resultado.html", out=out, texto=texto, now=datetime.now())

@app.get("/biblioteca")
def biblioteca():
    links = [
        # Constituição
        {
            "titulo": "Constituição Federal (PDF – DOU)",
            "url": "https://www.planalto.gov.br/ccivil_03/constituicao/DOUconstituicao88.pdf",
            "tipo": "Constituição"
        },
        {
            "titulo": "Constituição Federal (texto compilado)",
            "url": "https://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm",
            "tipo": "Constituição"
        },

        # Códigos
        {
            "titulo": "Código Penal",
            "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm",
            "tipo": "Código"
        },
        {
            "titulo": "Código de Processo Penal",
            "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm",
            "tipo": "Código"
        },
        {
            "titulo": "Código Civil",
            "url": "https://www.planalto.gov.br/ccivil_03/leis/2002/l10406compilada.htm",
            "tipo": "Código"
        },
        {
            "titulo": "Código de Processo Civil",
            "url": "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm",
            "tipo": "Código"
        },
        {
            "titulo": "Código de Defesa do Consumidor (CDC)",
            "url": "https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm",
            "tipo": "Código"
        },

        # Portais e bibliotecas
        {
            "titulo": "Portal da Legislação – Planalto",
            "url": "https://www4.planalto.gov.br/legislacao/portal-legis",
            "tipo": "Portal"
        },
        {
            "titulo": "Livros Abertos – Direito (acesso aberto)",
            "url": "https://www.livrosabertos.abcd.usp.br/portaldelivrosUSP/catalog/category/direito",
            "tipo": "Livros acadêmicos"
        },
        {
            "titulo": "Biblioteca Digital da OAB",
            "url": "http://www.oab.org.br/biblioteca-digital/publicacoes#",
            "tipo": "OAB"
        }
    ]

    return render_template("biblioteca.html", links=links)

@app.get("/sobre")
def sobre():
    return render_template("sobre.html")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
