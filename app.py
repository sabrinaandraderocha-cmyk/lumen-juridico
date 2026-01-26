import os
import re
from collections import Counter
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for, flash, abort
)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Bibliotecas de leitura de arquivos
from pypdf import PdfReader
from docx import Document as DocxDocument

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

# Garante que as pastas existam para o Render não dar erro de permissão
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(INSTANCE_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-lumen-2026")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024 
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

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
# Constantes e Biblioteca
# =========================
GLOSSARY_URL = "https://portal.stf.jus.br/jurisprudencia/glossario.asp"

LIBRARY_LINKS = [
    {"categoria": "CONSTITUIÇÃO", "titulo": "Constituição Federal", "url": "http://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm"},
    {"categoria": "CÓDIGO", "titulo": "Código Civil", "url": "http://www.planalto.gov.br/ccivil_03/leis/2002/l10406compilada.htm"},
    {"categoria": "CÓDIGO", "titulo": "Código de Processo Civil", "url": "http://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm"},
    {"categoria": "CÓDIGO", "titulo": "Código Penal", "url": "http://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm"},
    {"categoria": "CÓDIGO", "titulo": "Código de Processo Penal", "url": "http://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm"},
    {"categoria": "TRABALHISTA", "titulo": "Consolidação das Leis do Trabalho", "url": "http://www.planalto.gov.br/ccivil_03/decreto-lei/del5452compilado.htm"},
    {"categoria": "CONSUMIDOR", "titulo": "Código de Defesa do Consumidor", "url": "http://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm"},
    {"categoria": "ESTATUTO", "titulo": "Estatuto da Criança e Adolescente", "url": "http://www.planalto.gov.br/ccivil_03/leis/l8069.htm"},
    {"categoria": "PENAL ESPECIAL", "titulo": "Lei Maria da Penha", "url": "http://www.planalto.gov.br/ccivil_03/_ato2004-2006/2006/lei/l11340.htm"},
    {"categoria": "TRIBUTÁRIO", "titulo": "Código Tributário Nacional", "url": "http://www.planalto.gov.br/ccivil_03/leis/l5172compilado.htm"}
]

GLOSSARY_DICT = {
    "acórdão": "Decisão final proferida por um tribunal (grupo de juízes).",
    "prescrição": "Perda do direito de punir ou cobrar algo pelo passar do tempo.",
    "ementa": "Resumo oficial de uma decisão judicial.",
    "tempestivo": "Ato realizado dentro do prazo legal.",
    "preclusão": "Perda do direito de agir no processo por perda de prazo.",
}

# =========================
# Funções de Inteligência
# =========================

def ler_arquivo(caminho):
    ext = os.path.splitext(caminho)[1].lower()
    conteudo = ""
    try:
        if ext == ".pdf":
            reader = PdfReader(caminho)
            for page in reader.pages:
                conteudo += page.extract_text() or ""
        elif ext == ".docx":
            doc = DocxDocument(caminho)
            conteudo = "\n".join([p.text for p in doc.paragraphs])
    except: pass
    return conteudo

def build_output(text: str):
    keywords = [w for w, _ in Counter(re.findall(r'\w{6,}', text.lower())).most_common(5)]
    found_glossary = []
    for term, definition in GLOSSARY_DICT.items():
        if term in text.lower():
            found_glossary.append({"termo": term.title(), "definicao": definition})

    return {
        "tema_principal": f"Análise de {top_words[0].title() if keywords else 'Documento'}",
        "resumo": text[:800] + "...",
        "keywords": keywords,
        "glossario": found_glossary or [{"termo": "Processo", "definicao": "Atos judiciais"}]
    }

# =========================
# Rotas (Corrigidas)
# =========================

@app.route("/")
def home():
    recentes = Analise.query.order_by(Analise.id.desc()).limit(5).all()
    return render_template("index.html", historico=recentes)

@app.route("/analisar", methods=["POST"])
def analisar():
    texto = request.form.get("texto", "").strip()
    arquivo = request.files.get("arquivo")
    
    if arquivo and arquivo.filename:
        filename = secure_filename(arquivo.filename)
        path = os.path.join(UPLOAD_DIR, filename)
        arquivo.save(path)
        texto += "\n" + ler_arquivo(path)
        os.remove(path)

    if not texto or len(texto) < 10:
        flash("Texto insuficiente."); return redirect(url_for("home"))

    out = build_output(texto)
    nova = Analise(titulo_resumo=out["tema_principal"], texto_original=texto)
    db.session.add(nova); db.session.commit()
    return render_template("resultado.html", out=out, texto=texto, now=datetime.now(), analise_id=nova.id)

@app.route("/biblioteca")
def biblioteca():
    return render_template("biblioteca.html", links=LIBRARY_LINKS)

@app.route("/historico")
def historico():
    page = request.args.get('page', 1, type=int)
    analises = Analise.query.order_by(Analise.id.desc()).paginate(page=page, per_page=10)
    return render_template("historico.html", paginacao=analises)

@app.route("/sobre")
def sobre(): return render_template("sobre.html")

# ESTA ROTA ESTAVA FALTANDO E CAUSAVA O ERRO NO RENDER
@app.route("/glossario")
def glossario():
    return redirect(GLOSSARY_URL)

@app.route("/excluir/<int:id>")
def excluir(id):
    analise = Analise.query.get_or_404(id)
    db.session.delete(analise); db.session.commit()
    return redirect(url_for("historico"))

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
