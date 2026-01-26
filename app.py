import os
import re
from collections import Counter
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for, flash, abort
)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Bibliotecas de leitura (Certifique-se de que estão no requirements.txt)
from pypdf import PdfReader
from docx import Document as DocxDocument

# Banco de dados
from flask_sqlalchemy import SQLAlchemy

load_dotenv()

# =========================================================
# CONFIGURAÇÃO DO AMBIENTE
# =========================================================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
UPLOAD_DIR = os.path.join(INSTANCE_DIR, "uploads")
DB_PATH = os.path.join(INSTANCE_DIR, "lumen.db")

# Garante a existência das pastas no servidor
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(INSTANCE_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "lumen-juridico-key-2026")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # Limite de 16MB
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# =========================================================
# MODELO DE DADOS (HISTÓRICO)
# =========================================================
class Analise(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    titulo_resumo = db.Column(db.String(255))
    texto_original = db.Column(db.Text)
    tipo_peca = db.Column(db.String(100))

with app.app_context():
    db.create_all()

# =========================================================
# BIBLIOTECA JURÍDICA (TODOS OS LINKS DO SEU PRINT)
# =========================================================
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
    "acórdão": "Decisão proferida por um colegiado de juízes (tribunal).",
    "prescrição": "Perda do prazo para exercer o direito de ação.",
    "ementa": "Resumo oficial de uma decisão judicial.",
    "tempestivo": "Ato realizado dentro do prazo legal.",
    "preclusão": "Perda do direito de se manifestar no processo por decurso de prazo."
}

# =========================================================
# LÓGICA DE PROCESSAMENTO (IA & ARQUIVOS)
# =========================================================

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
    except Exception as e:
        print(f"Erro na leitura: {e}")
    return conteudo

def identificar_norma(texto, posicao):
    amostra = texto[max(0, posicao-100):posicao+100].lower()
    regras = {
        "penal": "Código Penal", "civil": "Código Civil", "cpc": "CPC",
        "trabalh": "CLT", "consumidor": "CDC", "constitui": "CF/88"
    }
    for chave, nome in regras.items():
        if chave in amostra: return nome
    return "Legislação não especificada"

def processar_texto(texto):
    # Detectar Artigos
    artigos = re.findall(r"(?:art\.?|artigo)\s*(\d+)", texto, re.I)
    fundamentos = []
    for num in artigos[:10]: # Limita aos 10 primeiros
        norma = identificar_norma(texto, texto.find(num))
        fundamentos.append(f"Art. {num} do {norma}")

    # Extrair palavras-chave (frequência)
    palavras = re.findall(r'\w{6,}', texto.lower())
    top_words = [w for w, _ in Counter(palavras).most_common(5)]

    # Glossário Dinâmico
    termos_encontrados = []
    for termo, desc in GLOSSARY_DICT.items():
        if termo in texto.lower():
            termos_encontrados.append({"termo": termo.title(), "definicao": desc})

    return {
        "titulo": f"Análise: {top_words[0].title() if top_words else 'Documento'}",
        "resumo": texto[:800] + "...",
        "normas": list(set(fundamentos)),
        "keywords": top_words,
        "glossario": termos_encontrados
    }

# =========================================================
# ROTAS DO SISTEMA
# =========================================================

@app.route("/")
def home():
    recentes = Analise.query.order_by(Analise.id.desc()).limit(3).all()
    return render_template("index.html", historico=recentes)

@app.route("/analisar", methods=["POST"])
def analisar():
    texto_input = request.form.get("texto", "").strip()
    arquivo = request.files.get("arquivo")
    
    texto_final = texto_input
    if arquivo and arquivo.filename:
        filename = secure_filename(arquivo.filename)
        path = os.path.join(UPLOAD_DIR, filename)
        arquivo.save(path)
        texto_final += "\n" + ler_arquivo(path)
        os.remove(path) # Limpeza

    if not texto_final or len(texto_final) < 20:
        flash("Por favor, insira um texto mais longo ou um arquivo válido.")
        return redirect(url_for("home"))

    resultado = processar_texto(texto_final)
    
    # Salvar no Banco
    nova_analise = Analise(titulo_resumo=resultado["titulo"], texto_original=texto_final)
    db.session.add(nova_analise)
    db.session.commit()

    return render_template("resultado.html", out=resultado, texto=texto_final, id=nova_analise.id)

@app.route("/biblioteca")
def biblioteca():
    return render_template("biblioteca.html", links=LIBRARY_LINKS)

@app.route("/historico")
def historico():
    page = request.args.get('page', 1, type=int)
    dados = Analise.query.order_by(Analise.id.desc()).paginate(page=page, per_page=10)
    return render_template("historico.html", paginacao=dados)

@app.route("/excluir/<int:id>")
def excluir(id):
    item = Analise.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("historico"))

@app.route("/sobre")
def sobre():
    return render_template("sobre.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
