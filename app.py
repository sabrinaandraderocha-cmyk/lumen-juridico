import os
import re
from collections import Counter
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for, flash, abort
)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Leitura de arquivos
from pypdf import PdfReader
from docx import Document

# Banco de dados
from flask_sqlalchemy import SQLAlchemy

load_dotenv()

# =========================
# Configuração do App
# =========================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
UPLOAD_DIR = os.path.join(INSTANCE_DIR, "uploads")
DB_PATH = os.path.join(INSTANCE_DIR, "lumen.db")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(INSTANCE_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-change-me")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
ALLOWED_EXTS = {".pdf", ".docx", ".txt"}

# =========================
# Modelo do Banco de Dados
# =========================
class Analise(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    titulo_resumo = db.Column(db.String(255))
    texto_original = db.Column(db.Text)
    tipo_peca = db.Column(db.String(100))
    
    def __repr__(self):
        return f'<Analise {self.id}>'

with app.app_context():
    db.create_all()

# =========================
# Biblioteca e Glossário
# =========================
GLOSSARY_URL = "https://portal.stf.jus.br/jurisprudencia/glossario.asp"

LIBRARY_LINKS = [
    # --- Legislação Fundamental ---
    {"key": "CF_HTML", "titulo": "Constituição Federal", "url": "https://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm", "tipo": "Constituição"},
    {"key": "CC", "titulo": "Código Civil", "url": "https://www.planalto.gov.br/ccivil_03/leis/2002/l10406compilada.htm", "tipo": "Código"},
    {"key": "CPC", "titulo": "Código de Processo Civil (CPC)", "url": "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm", "tipo": "Código"},
    {"key": "CP", "titulo": "Código Penal (CP)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm", "tipo": "Código"},
    {"key": "CPP", "titulo": "Código de Processo Penal (CPP)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm", "tipo": "Código"},
    {"key": "CLT", "titulo": "Consolidação das Leis do Trabalho (CLT)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del5452.htm", "tipo": "Trabalhista"},
    {"key": "CDC", "titulo": "Código de Defesa do Consumidor", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm", "tipo": "Consumidor"},
    
    # --- Legislação Específica ---
    {"key": "CTN", "titulo": "Código Tributário Nacional", "url": "https://www.planalto.gov.br/ccivil_03/leis/l5172.htm", "tipo": "Tributário"},
    {"key": "LIC", "titulo": "Lei de Licitações (14.133/21)", "url": "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/L14133.htm", "tipo": "Administrativo"},
    {"key": "LIA", "titulo": "Lei de Improbidade Administrativa", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8429.htm", "tipo": "Administrativo"},
    {"key": "ECA", "titulo": "Estatuto da Criança e Adolescente", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8069.htm", "tipo": "Estatuto"},
    {"key": "MPENHA", "titulo": "Lei Maria da Penha", "url": "https://www.planalto.gov.br/ccivil_03/_ato2004-2006/2006/lei/l11340.htm", "tipo": "Penal Especial"},
    
    # --- Cursos Gratuitos ---
    {"key": "CURSO_STF", "titulo": "Cursos EAD – Supremo Tribunal Federal", "url": "https://ead.stf.jus.br/course/index.php?categoryid=3", "tipo": "🎓 Curso Gratuito"},
    {"key": "CURSO_ESA", "titulo": "ESA OAB – Cursos Gratuitos", "url": "https://esa.oab.org.br/home/ver-cursos?filter_categories_id%5B%5D=24", "tipo": "🎓 Curso Gratuito"},
    {"key": "CURSO_GOV", "titulo": "Escola Virtual Gov (EV.G) – Direito", "url": "https://www.escolavirtual.gov.br/catalogo", "tipo": "🎓 Curso Gratuito"},

    # --- Ferramentas ---
    {"key": "STF_GLOSS", "titulo": "Glossário Jurídico STF", "url": GLOSSARY_URL, "tipo": "Ferramenta"},
]

STOPWORDS_PT = {
    "a","o","os","as","um","uma","uns","umas","de","do","da","dos","das","em","no","na","nos","nas",
    "por","para","com","sem","sobre","entre","e","ou","que","se","ao","aos","à","às","como",
    "art","artigo","lei","decreto","tribunal","stj","stf","processo","recurso","ementa","autos",
    "vistos", "vossa", "excelência", "parte", "folhas"
}

# =========================
# Lógica de Inteligência
# =========================

def normalize(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text

def smart_summary(text: str) -> str:
    normalized = normalize(text)
    lower_text = normalized.lower()
    
    ementa_match = re.search(r"\bementa\b[:\s]*(.*?)(?:\bac[oó]rd[aã]o\b|\brelat[oó]rio\b|$)", lower_text, re.DOTALL)
    if ementa_match and len(ementa_match.group(1)) > 50:
        return normalized[ementa_match.start(1):ementa_match.end(1)].strip()[:800] + "..."

    intro_patterns = [
        r"(trata-se de\s+.*?\.)",
        r"(cuida-se de\s+.*?\.)",
        r"(o cerne da questão\s+.*?\.)",
        r"(a controvérsia cinge-se\s+.*?\.)"
    ]
    for pat in intro_patterns:
        match = re.search(pat, lower_text, re.IGNORECASE | re.DOTALL)
        if match:
            start = match.start()
            return "SÍNTESE DETECTADA: " + normalized[start:start+600].strip() + "..."

    return normalized[:600] + "..."

def extract_articles_with_context(text: str) -> list[str]:
    law_map = {
        "cc": "Código Civil", "civil": "Código Civil", "10.406": "Código Civil",
        "cpc": "CPC", "processo civil": "CPC", "13.105": "CPC",
        "cp": "Código Penal", "penal": "Código Penal", "2.848": "Código Penal",
        "cpp": "CPP", "processo penal": "CPP",
        "cdc": "CDC", "consumidor": "CDC", "8.078": "CDC",
        "cf": "Constituição Federal", "constituição": "Constituição Federal",
        "clt": "CLT", "trabalho": "CLT"
    }

    found_citations = []
    article_pattern = re.compile(r"\b(?:art\.?|artigo)\s*(\d+[º°]?[-\w]*)", re.IGNORECASE)
    
    for match in article_pattern.finditer(text):
        article_num = match.group(1)
        start_pos = match.start()
        window = text[max(0, start_pos - 150):min(len(text), start_pos + 150)].lower()
        
        detected_law = "Lei não identificada"
        for key, law_name in law_map.items():
            if key in window:
                detected_law = law_name
                break
        
        citation = f"Art. {article_num} do {detected_law}"
        if citation not in found_citations:
            found_citations.append(citation)
            
    return found_citations[:15]

def build_smart_search_queries(keywords, citations):
    queries = []
    main_keyword = keywords[0] if keywords else "Jurisprudência"
    best_citation = next((c for c in citations if "não identificada" not in c), None)
    
    if best_citation:
        clean_cit = best_citation.replace("do ", "").replace("da ", "")
        queries.append(f"Jurisprudência {clean_cit} {main_keyword}")
        queries.append(f"Informativo STJ {clean_cit}")
    
    if len(keywords) >= 3:
        topic = " ".join(keywords[:3])
        queries.append(f"Tese jurídica {topic} recente")
        
    return queries[:4]

def pick_keywords(text: str, k=6):
    clean = re.sub(r'[^\w\s]', '', text.lower())
    tokens = [t for t in clean.split() if t not in STOPWORDS_PT and len(t) > 3 and not t.isdigit()]
    return [w for w, _ in Counter(tokens).most_common(k)]

def build_output(text: str):
    text = normalize(text)
    resumo = smart_summary(text)
    keywords = pick_keywords(text)
    fundamentos = extract_articles_with_context(text)
    pesquisas = build_smart_search_queries(keywords, fundamentos)
    
    juris_refs = list(set(re.findall(r"(Súmula\s*\d+|Tema\s*\d+|REsp\s*[\d\.]+)", text, re.I)))

    return {
        "tema_principal": f"{', '.join(keywords[:3]).title()}" if keywords else "Análise Jurídica",
        "resumo": resumo,
        "fundamentos_normas": fundamentos,
        "fundamentos_juris": juris_refs,
        "keywords": keywords,
        "queries_juris": pesquisas,
        "sugestoes": LIBRARY_LINKS[:8],
        "alerta": "Texto excessivamente curto - análise limitada." if len(text) < 500 else None
    }

# =========================
# Helpers de Arquivo
# =========================
def get_text_from_upload(file):
    filename = secure_filename(file.filename)
    if not filename: return ""
    ext = os.path.splitext(filename)[1].lower()
    path = os.path.join(UPLOAD_DIR, filename)
    file.save(path)
    text = ""
    try:
        if ext == ".pdf":
            reader = PdfReader(path)
            text = "\n".join([p.extract_text() or "" for p in reader.pages])
        elif ext == ".docx":
            doc = Document(path)
            text = "\n".join([p.text for p in doc.paragraphs])
        elif ext == ".txt":
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
    finally:
        if os.path.exists(path): os.remove(path)
    return text

# =========================
# Rotas
# =========================
@app.route("/")
def home():
    historico = Analise.query.order_by(Analise.data_criacao.desc()).limit(5).all()
    return render_template("index.html", historico=historico)

@app.route("/analisar", methods=["POST"])
def analisar():
    texto = request.form.get("texto", "").strip()
    arquivo = request.files.get("arquivo")
    if arquivo and arquivo.filename:
        texto = f"{texto}\n\n{get_text_from_upload(arquivo)}".strip()
    
    if not texto or len(texto) < 10:
        flash("Documento ou texto insuficiente.", "error")
        return redirect(url_for("home"))

    out = build_output(texto)
    nova = Analise(titulo_resumo=out["tema_principal"], texto_original=texto, tipo_peca="Jurídico")
    db.session.add(nova)
    db.session.commit()
    return render_template("resultado.html", out=out, texto=texto, now=datetime.now(), analise_id=nova.id)

@app.route("/resultado/<int:id>")
def resultado(id):
    analise = Analise.query.get_or_404(id)
    out = build_output(analise.texto_original)
    return render_template("resultado.html", out=out, texto=analise.texto_original, now=datetime.now(), analise_id=analise.id)

@app.route("/historico")
def historico():
    page = request.args.get('page', 1, type=int)
    analises = Analise.query.order_by(Analise.data_criacao.desc()).paginate(page=page, per_page=10)
    return render_template("historico.html", paginacao=analises)

@app.route("/excluir/<int:id>")
def excluir(id):
    analise = Analise.query.get_or_404(id)
    db.session.delete(analise)
    db.session.commit()
    flash("Análise removida.", "success")
    return redirect(url_for("home"))

@app.route("/biblioteca")
def biblioteca():
    return render_template("biblioteca.html", links=LIBRARY_LINKS)

@app.route("/sobre")
def sobre():
    return render_template("sobre.html")

@app.route("/glossario")
def glossario():
    return redirect(GLOSSARY_URL)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
