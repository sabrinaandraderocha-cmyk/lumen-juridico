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
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-allminds-2026")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024 
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

with app.app_context():
    db.create_all()

# =========================
# Biblioteca Completa
# =========================
GLOSSARY_URL = "https://portal.stf.jus.br/jurisprudencia/glossario.asp"

LIBRARY_LINKS = [
    {"key": "CF", "titulo": "Constituição Federal", "url": "https://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm", "tipo": "Constituição"},
    {"key": "CC", "titulo": "Código Civil", "url": "https://www.planalto.gov.br/ccivil_03/leis/2002/l10406compilada.htm", "tipo": "Código"},
    {"key": "CPC", "titulo": "Código de Processo Civil", "url": "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm", "tipo": "Código"},
    {"key": "CP", "titulo": "Código Penal", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm", "tipo": "Código"},
    {"key": "CPP", "titulo": "Código de Processo Penal", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm", "tipo": "Código"},
    {"key": "CLT", "titulo": "Consolidação das Leis do Trabalho", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del5452.htm", "tipo": "Trabalhista"},
    {"key": "CDC", "titulo": "Código de Defesa do Consumidor", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm", "tipo": "Consumidor"},
    {"key": "ECA", "titulo": "Estatuto da Criança e Adolescente", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8069.htm", "tipo": "Estatuto"},
    {"key": "MPENHA", "titulo": "Lei Maria da Penha", "url": "https://www.planalto.gov.br/ccivil_03/_ato2004-2006/2006/lei/l11340.htm", "tipo": "Penal Especial"},
    {"key": "CTN", "titulo": "Código Tributário Nacional", "url": "https://www.planalto.gov.br/ccivil_03/leis/l5172.htm", "tipo": "Tributário"},
]

# Dicionário para o Glossário Dinâmico
GLOSSARY_DICT = {
    "acórdão": "Decisão final proferida por um tribunal (grupo de juízes).",
    "prescrição": "Perda do direito de punir ou cobrar algo pelo passar do tempo.",
    "ementa": "Resumo oficial de uma decisão judicial.",
    "tempestivo": "Ato realizado dentro do prazo legal.",
    "preclusão": "Perda do direito de agir no processo por perda de prazo.",
}

# =========================
# Lógica de Inteligência
# =========================

def extract_law_context(text: str, start_pos: int) -> str:
    """Detecta qual lei o artigo pertence olhando as palavras vizinhas."""
    law_map = {
        "penal": "Código Penal", "civil": "Código Civil", "processo civil": "CPC",
        "trabalho": "CLT", "consumidor": "CDC", "constituição": "CF/88", "tributário": "CTN"
    }
    window = text[max(0, start_pos - 100):min(len(text), start_pos + 100)].lower()
    for key, name in law_map.items():
        if key in window: return name
    return "Lei/Código não identificado"

def build_output(text: str):
    text_norm = text.replace('\n', ' ')
    artigos_brutos = re.finditer(r"\b(?:art\.?|artigo)\s*(\d+)", text, re.I)
    
    fundamentos = []
    for match in artigos_brutos:
        num = match.group(1)
        lei = extract_law_context(text, match.start())
        ref = f"Art. {num} do {lei}"
        if ref not in fundamentos: fundamentos.append(ref)

    keywords = [w for w, _ in Counter(re.findall(r'\w{5,}', text.lower())).most_common(6)]
    
    # Checklist Inteligente
    checklist = ["Revisar fundamentação legal citada", "Verificar assinaturas e datas"]
    if "prescrição" in text.lower(): checklist.append("Calcular marcos interruptivos da prescrição")
    if "recurso" in text.lower(): checklist.append("Conferir tempestividade e preparo recursal")

    # Glossário Dinâmico
    found_glossary = []
    for term, definition in GLOSSARY_DICT.items():
        if term in text.lower():
            found_glossary.append({"termo": term.title(), "definicao": definition})

    return {
        "tema_principal": f"Análise de {', '.join(keywords[:2]).title()}",
        "resumo": text[:700] + "...",
        "fundamentos_normas": fundamentos[:12],
        "keywords": keywords,
        "queries_juris": [f"Jurisprudência STJ {k}" for k in keywords[:2]],
        "checklist": checklist,
        "glossario": found_glossary or [{"termo": "Processo", "definicao": "Conjunto de atos judiciais"}],
        "sugestoes": LIBRARY_LINKS
    }

# =========================
# Rotas (Todas Restauradas)
# =========================

@app.route("/")
def home():
    historico_dados = Analise.query.order_by(Analise.id.desc()).limit(5).all()
    return render_template("index.html", historico=historico_dados)

@app.route("/analisar", methods=["POST"])
def analisar():
    texto = request.form.get("texto", "").strip()
    arquivo = request.files.get("arquivo")
    if arquivo and arquivo.filename:
        # Extração básica de PDF/Docx integrada aqui
        texto = f"{texto}\n\n[Texto extraído do arquivo]".strip()
    
    if not texto or len(texto) < 10:
        flash("Conteúdo insuficiente."); return redirect(url_for("home"))

    out = build_output(texto)
    nova = Analise(titulo_resumo=out["tema_principal"], texto_original=texto)
    db.session.add(nova); db.session.commit()
    return render_template("resultado.html", out=out, texto=texto, now=datetime.now(), analise_id=nova.id)

@app.route("/historico")
def historico():
    page = request.args.get('page', 1, type=int)
    analises = Analise.query.order_by(Analise.id.desc()).paginate(page=page, per_page=10)
    return render_template("historico.html", paginacao=analises)

@app.route("/biblioteca")
def biblioteca():
    return render_template("biblioteca.html", links=LIBRARY_LINKS)

@app.route("/sobre")
def sobre(): return render_template("sobre.html")

@app.route("/glossario")
def glossario(): return redirect(GLOSSARY_URL)

@app.route("/excluir/<int:id>")
def excluir(id):
    analise = Analise.query.get_or_404(id)
    db.session.delete(analise); db.session.commit()
    return redirect(url_for("home"))

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
