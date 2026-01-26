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
    
    def __repr__(self):
        return f'<Analise {self.id}>'

with app.app_context():
    db.create_all()

# =========================
# Dados Estáticos (Biblioteca e Dicionário)
# =========================
GLOSSARY_URL = "https://portal.stf.jus.br/jurisprudencia/glossario.asp"

LIBRARY_LINKS = [
    {"key": "CF_HTML", "titulo": "Constituição Federal", "url": "https://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm", "tipo": "Constituição"},
    {"key": "CC", "titulo": "Código Civil", "url": "https://www.planalto.gov.br/ccivil_03/leis/2002/l10406compilada.htm", "tipo": "Código"},
    {"key": "CPC", "titulo": "Código de Processo Civil", "url": "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm", "tipo": "Código"},
    {"key": "CP", "titulo": "Código Penal", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm", "tipo": "Código"},
    {"key": "CPP", "titulo": "Código de Processo Penal", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm", "tipo": "Código"},
    {"key": "CLT", "titulo": "Consolidação das Leis do Trabalho", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del5452.htm", "tipo": "Trabalhista"},
    {"key": "CDC", "titulo": "Código de Defesa do Consumidor", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm", "tipo": "Consumidor"},
    {"key": "MPENHA", "titulo": "Lei Maria da Penha", "url": "https://www.planalto.gov.br/ccivil_03/_ato2004-2006/2006/lei/l11340.htm", "tipo": "Penal Especial"},
]

GLOSSARY_DICT = {
    "acórdão": "Decisão final proferida por um tribunal (grupo de juízes).",
    "prescrição": "Perda do direito de punir ou cobrar algo devido ao passar do tempo.",
    "decadência": "Perda do próprio direito pelo não exercício no prazo legal.",
    "ementa": "Resumo oficial de uma decisão judicial.",
    "trânsito em julgado": "Quando não cabe mais nenhum recurso; a decisão é definitiva."
}

# =========================
# Lógica de Inteligência
# =========================

def build_output(text: str):
    artigos = re.findall(r"\b(?:art\.?|artigo)\s*(\d+)", text, re.I)
    keywords = [w for w, _ in Counter(re.findall(r'\w{5,}', text.lower())).most_common(5)]
    
    # Checklist Básico
    checklist = ["Verificar prazos processuais", "Conferir fundamentação legal citada"]
    if "prescrição" in text.lower():
        checklist.append("Calcular marco interruptivo da prescrição")

    # Glossário Dinâmico
    glossario = []
    for termo, definicao in GLOSSARY_DICT.items():
        if termo in text.lower():
            glossario.append({"termo": termo.title(), "definicao": definicao})

    return {
        "tema_principal": f"Análise de {', '.join(keywords[:2]).title()}",
        "resumo": text[:800] + "...",
        "fundamentos_normas": [f"Art. {a}" for a in set(artigos[:10])],
        "keywords": keywords,
        "queries_juris": [f"Jurisprudência STJ {k}" for k in keywords[:2]],
        "checklist": checklist,
        "glossario": glossario or [{"termo": "Acórdão", "definicao": "Decisão de um tribunal"}],
        "sugestoes": LIBRARY_LINKS[:5]
    }

def get_text_from_upload(file):
    filename = secure_filename(file.filename)
    path = os.path.join(UPLOAD_DIR, filename)
    file.save(path)
    text = ""
    try:
        if filename.endswith(".pdf"):
            text = "\n".join([p.extract_text() or "" for p in PdfReader(path).pages])
        elif filename.endswith(".docx"):
            text = "\n".join([p.text for p in Document(path).paragraphs])
        else:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f: text = f.read()
    finally:
        if os.path.exists(path): os.remove(path)
    return text

# =========================
# Rotas (Todas as Funções Restauradas)
# =========================

@app.route("/")
def home():
    # Carrega as últimas análises para a página inicial
    historico_dados = Analise.query.order_by(Analise.data_criacao.desc()).limit(5).all()
    return render_template("index.html", historico=historico_dados)

@app.route("/analisar", methods=["POST"])
def analisar():
    texto = request.form.get("texto", "").strip()
    arquivo = request.files.get("arquivo")
    if arquivo and arquivo.filename:
        texto = f"{texto}\n\n{get_text_from_upload(arquivo)}".strip()
    
    if not texto or len(texto) < 10:
        flash("Conteúdo insuficiente para análise.", "error")
        return redirect(url_for("home"))

    out = build_output(texto)
    nova = Analise(titulo_resumo=out["tema_principal"], texto_original=texto)
    db.session.add(nova)
    db.session.commit()
    return render_template("resultado.html", out=out, texto=texto, now=datetime.now(), analise_id=nova.id)

@app.route("/historico")
def historico():
    page = request.args.get('page', 1, type=int)
    analises = Analise.query.order_by(Analise.data_criacao.desc()).paginate(page=page, per_page=10)
    return render_template("historico.html", paginacao=analises)

@app.route("/resultado/<int:id>")
def resultado(id):
    analise = Analise.query.get_or_404(id)
    out = build_output(analise.texto_original)
    return render_template("resultado.html", out=out, texto=analise.texto_original, now=datetime.now(), analise_id=analise.id)

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
    # Porta padrão para o Render (10000)
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
