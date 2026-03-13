import os
import json
import re
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

# IA Generativa
import google.generativeai as genai

load_dotenv()

# =========================
# Configuração do App e IA
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
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
ALLOWED_EXTS = {".pdf", ".docx", ".txt"}

# Configuração da Chave da API do Gemini (adicione GEMINI_API_KEY no seu .env)
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

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
        return f"<Analise {self.id}>"

with app.app_context():
    db.create_all()

# =========================
# Glossário, Biblioteca e Artigos (MANTIDOS)
# =========================
GLOSSARY_URL = "https://portal.stf.jus.br/jurisprudencia/glossario.asp"

LIBRARY_LINKS = [
    {"key": "CF_HTML", "titulo": "Constituição Federal (Compilado)", "url": "https://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm", "tipo": "Constituição"},
    {"key": "CC", "titulo": "Código Civil", "url": "https://www.planalto.gov.br/ccivil_03/leis/2002/l10406compilada.htm", "tipo": "Código"},
    {"key": "CPC", "titulo": "Código de Processo Civil (CPC)", "url": "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm", "tipo": "Código"},
    {"key": "CP", "titulo": "Código Penal (CP)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm", "tipo": "Código"},
    {"key": "CPP", "titulo": "Código de Processo Penal (CPP)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm", "tipo": "Código"},
    {"key": "CLT", "titulo": "Consolidação das Leis do Trabalho (CLT)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del5452.htm", "tipo": "Trabalhista"},
    {"key": "CDC", "titulo": "Código de Defesa do Consumidor", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm", "tipo": "Consumidor"},
    {"key": "CURSO_STF", "titulo": "Cursos EAD – Supremo Tribunal Federal", "url": "https://ead.stf.jus.br/course/index.php?categoryid=3", "tipo": "🎓 Curso Gratuito"},
    {"key": "CURSO_ESA", "titulo": "ESA OAB – Cursos Gratuitos", "url": "https://esa.oab.org.br/home/ver-cursos?filter_categories_id%5B%5D=24", "tipo": "🎓 Curso Gratuito"},
    {"key": "CURSO_GOV", "titulo": "Escola Virtual Gov (EV.G) – Direito", "url": "https://www.escolavirtual.gov.br/catalogo", "tipo": "🎓 Curso Gratuito"},
    {"key": "CTN", "titulo": "Código Tributário Nacional", "url": "https://www.planalto.gov.br/ccivil_03/leis/l5172.htm", "tipo": "Tributário"},
    {"key": "LIC", "titulo": "Lei de Licitações (14.133/21)", "url": "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/L14133.htm", "tipo": "Administrativo"},
    {"key": "LIA", "titulo": "Lei de Improbidade Administrativa", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8429.htm", "tipo": "Administrativo"},
    {"key": "ECA", "titulo": "Estatuto da Criança e Adolescente", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8069.htm", "tipo": "Estatuto"},
    {"key": "MPENHA", "titulo": "Lei Maria da Penha", "url": "https://www.planalto.gov.br/ccivil_03/_ato2004-2006/2006/lei/l11340.htm", "tipo": "Penal Especial"},
    {"key": "STF_GLOSS", "titulo": "Glossário Jurídico STF", "url": GLOSSARY_URL, "tipo": "Ferramenta"},
]

TERM_TRANSLATIONS = {
    "habeas corpus": "pedido para proteger a liberdade (contra prisão ilegal/abuso).",
    "periculum libertatis": "risco ligado à liberdade do acusado (perigo concreto de solto).",
    "fumus boni iuris": "aparência de bom direito (indícios de que o pedido faz sentido).",
    "periculum in mora": "risco da demora (se esperar, o direito pode se perder).",
    "ratio decidendi": "motivo central que sustentou a decisão (fundamento decisivo).",
    "obiter dictum": "comentário do julgador que não foi essencial para decidir.",
    "distinguishing": "diferenciar o caso do precedente por fatos distintos.",
    "overruling": "superação de entendimento anterior (mudança de jurisprudência).",
    "nulidade": "ato/processo inválido por violação de regra/garantia.",
    "ônus da prova": "quem tem o dever de provar determinado fato.",
    "tutela de urgência": "decisão rápida e provisória para evitar dano imediato.",
    "prisão preventiva": "prisão antes da sentença para proteger o processo/sociedade.",
    "trânsito em julgado": "quando não cabe mais recurso da decisão.",
    "in dubio pro reo": "na dúvida, decide-se a favor do réu.",
    "ex tunc": "efeito retroativo (vale desde o início).",
    "ex nunc": "efeito não retroativo (vale daqui para frente)."
}

ARTICLE_DB = [
    {"titulo": "Precedentes obrigatórios e segurança jurídica no CPC/2015", "autores": "Daniel Mitidiero", "onde": "Revista de Processo (RT)", "ano": "2016", "codigo_relacionado": ["CPC"], "area": ["Processo Civil", "Precedentes"], "url": ""},
    {"titulo": "O sistema de precedentes no CPC/2015", "autores": "Fredie Didier Jr.", "onde": "Doutrina processual", "ano": "2015-2018", "codigo_relacionado": ["CPC"], "area": ["Processo Civil"], "url": ""},
    {"titulo": "Prisão preventiva e fundamentação", "autores": "Aury Lopes Jr.", "onde": "Doutrina processual penal", "ano": "2019-2023", "codigo_relacionado": ["CPP", "CF"], "area": ["Processo Penal", "Prisão"], "url": ""},
    {"titulo": "Responsabilidade civil: nexo causal, dano", "autores": "Sérgio Cavalieri Filho", "onde": "Doutrina civil", "ano": "2010-2022", "codigo_relacionado": ["CC", "CF"], "area": ["Civil", "Danos"], "url": ""},
    {"titulo": "Dever de motivação das decisões judiciais", "autores": "Lenio Streck", "onde": "Doutrina constitucional", "ano": "2014-2021", "codigo_relacionado": ["CF", "CPC", "CPP"], "area": ["Constitucional"], "url": ""},
    {"titulo": "Tutela de urgência e requisitos", "autores": "Humberto Theodoro Júnior", "onde": "Doutrina processual civil", "ano": "2016-2022", "codigo_relacionado": ["CPC"], "area": ["Processo Civil"], "url": ""},
    {"titulo": "Vulnerabilidade e proteção do consumidor", "autores": "Cláudia Lima Marques", "onde": "Doutrina consumidor", "ano": "2000-2020", "codigo_relacionado": ["CDC", "CF"], "area": ["Consumidor"], "url": ""},
]

# =========================
# Integração com a IA Generativa (NOVO)
# =========================
def analyze_with_ai(text: str) -> dict:
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"""
    Você é um assistente jurídico avançado do aplicativo Lumen Jurídico.
    Analise o texto jurídico fornecido e extraia as informações essenciais.
    Retorne ESTRITAMENTE um objeto JSON válido com as seguintes chaves e tipos de dados:
    
    {{
        "tema_principal": "string (ex: Responsabilidade Civil por Acidente, Prisão Preventiva)",
        "area_direito": "string (Ramo do direito principal, ex: Processo Civil, Penal, Constitucional)",
        "tipo_peca": "string (ex: Petição Inicial, Sentença, Acórdão, Habeas Corpus)",
        "tribunal": "string (ex: STF, STJ, TJMG, TRF1, ou vazio se não houver)",
        "fatos_relevantes": "string (resumo narrativo de 3 a 5 frases do que ocorreu no caso)",
        "controversia": "string (a questão jurídica central formulada como uma pergunta clara)",
        "fundamentos_normativos": ["array de strings", "apenas os principais artigos e leis citados, ex: art. 5º da CF"],
        "fundamentos_juris": ["array de strings", "jurisprudências e súmulas citadas, ex: Súmula 7 do STJ"],
        "dispositivo_resultado": "string (o que foi decidido ao final, se houver, ou o que está sendo pedido, em linguagem clara)",
        "codigos_relacionados": ["array de strings", "siglas das leis aplicáveis, ex: CC, CP, CPC, CPP, CF, CDC"],
        "palavras_chave": ["array de strings", "5 palavras fundamentais do texto"],
        "checklist": ["array de strings", "3 ações práticas sugeridas para o advogado focar ao lidar com este caso"]
    }}
    
    Texto para análise:
    {text[:25000]} 
    """
    
    try:
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(response_mime_type="application/json")
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Erro na IA: {e}")
        return {}

# =========================
# Funções Auxiliares Retidas
# =========================
def normalize(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text

def extract_terms_translation(text: str, max_items: int = 10) -> list[dict]:
    t = (text or "").lower()
    hits = []
    seen = set()
    for term, tr in TERM_TRANSLATIONS.items():
        if term in t and term not in seen:
            seen.add(term)
            hits.append({"termo": term, "traducao": tr})
            if len(hits) >= max_items:
                break
    return hits

def recommend_articles(codes: list[str], area: str, max_items: int = 6) -> list[dict]:
    codes = codes or []
    out = []
    for a in ARTICLE_DB:
        ok_code = any(c in (a.get("codigo_relacionado") or []) for c in codes) if codes else False
        ok_area = any(area_part.lower() in (area or "").lower() for area_part in (a.get("area") or []))
        if ok_code or ok_area:
            out.append(a)
            
    if not out:
        for a in ARTICLE_DB:
            if any(c in (a.get("codigo_relacionado") or []) for c in ["CF", "CPC", "CPP"]):
                out.append(a)

    seen, uniq = set(), []
    for a in out:
        k = (a.get("titulo") or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            uniq.append(a)
        if len(uniq) >= max_items:
            break
    return uniq

def suggest_library_links(text: str, max_items: int = 7):
    t = (text or "").lower()
    out = []
    for link in LIBRARY_LINKS:
        keywords = link["titulo"].split()
        matches = sum(1 for k in keywords if len(k) > 3 and k.lower() in t)
        if matches >= 1:
            out.append(link)

    if not out:
        out = [l for l in LIBRARY_LINKS if l["key"] in ["CF_HTML", "CPC", "STF_GLOSS"]]

    seen, unique_out = set(), []
    for x in out:
        if x["key"] not in seen:
            unique_out.append(x)
            seen.add(x["key"])
    return unique_out[:max_items]

def build_search_queries(pergunta: str, keywords: list[str], tribunal: str) -> list[str]:
    kws = [k for k in (keywords or [])][:3]
    out = []
    if kws:
        out.append(" AND ".join([f'"{k}"' for k in kws]))
    if pergunta:
        out.append(pergunta)
    th = (tribunal or "").upper().strip()
    if th in ["STJ", "STF"]:
        out.append(f"site:{th.lower()}.jus.br {' '.join(kws)}".strip())
    else:
        out.append(f"jurisprudência {' '.join(kws)}".strip())
    return out

# =========================
# Lógica Principal Unificada
# =========================
def build_output(text: str):
    texto_limpo = normalize(text)
    
    # Processamento Inteligente via LLM
    dados_ia = analyze_with_ai(texto_limpo)
    
    # Fallbacks caso a IA retorne vazio
    area = dados_ia.get("area_direito", "Geral")
    codigos = dados_ia.get("codigos_relacionados", [])
    keywords = dados_ia.get("palavras_chave", [])
    pergunta = dados_ia.get("controversia", "Qual é a controvérsia jurídica principal deste caso?")
    tribunal = dados_ia.get("tribunal", "")

    # Cruzamento com a base de dados estática do Lumen
    artigos = recommend_articles(codigos, area, max_items=6)
    sugestoes = suggest_library_links(texto_limpo)
    termos_importantes = extract_terms_translation(texto_limpo)
    pesquisas = build_search_queries(pergunta, keywords, tribunal)
    
    return {
        "tema_principal": dados_ia.get("tema_principal", "Análise Jurídica"),
        "area_sugerida": area,
        "codigos_relacionados": codigos,
        "meta": {
            "tribunal": tribunal,
            "tipo_peca_detectado": dados_ia.get("tipo_peca", "Documento Jurídico")
        },
        "sintaxe_caso": {
            "fatos_relevantes": dados_ia.get("fatos_relevantes", "Fatos não puderam ser extraídos."),
            "controversia": pergunta,
            "resultado_dispositivo": dados_ia.get("dispositivo_resultado", "Dispositivo não encontrado."),
        },
        "fundamentos_normas": dados_ia.get("fundamentos_normativos", []),
        "fundamentos_juris": dados_ia.get("fundamentos_juris", []),
        "keywords": keywords,
        "queries_juris": pesquisas,
        "checklist": dados_ia.get("checklist", []),
        "resumo": dados_ia.get("fatos_relevantes", ""), # Usando fatos como resumo principal
        "termos_importantes": termos_importantes,
        "sugestoes": sugestoes,
        "artigos_recomendados": artigos,
        "glossario_source": GLOSSARY_URL,
        "alerta": "" # Removido o alerta de tamanho pois o LLM lida bem com isso
    }

# =========================
# Upload Helpers
# =========================
def allowed_file(filename):
    return os.path.splitext((filename or "").lower())[1] in ALLOWED_EXTS

def get_text_from_upload(file):
    filename = secure_filename(file.filename or "")
    if not filename:
        return ""

    ext = os.path.splitext(filename)[1].lower()
    path = os.path.join(UPLOAD_DIR, f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}")
    file.save(path)

    text = ""
    try:
        if ext == ".pdf":
            reader = PdfReader(path)
            parts = []
            for p in reader.pages:
                try:
                    parts.append(p.extract_text() or "")
                except Exception:
                    parts.append("")
            text = "\n".join(parts)
        elif ext == ".docx":
            doc = Document(path)
            text = "\n".join([p.text for p in doc.paragraphs if (p.text or "").strip()])
        elif ext == ".txt":
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
    except Exception as e:
        print(f"Erro ao ler arquivo: {e}")
    finally:
        try:
            os.remove(path)
        except Exception:
            pass
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
    texto = (request.form.get("texto") or "").strip()
    arquivo = request.files.get("arquivo")

    if arquivo and arquivo.filename:
        if not allowed_file(arquivo.filename):
            flash("Envie apenas PDF, DOCX ou TXT.", "error")
            return redirect(url_for("home"))
        extraido = get_text_from_upload(arquivo)
        texto = f"{texto}\n\n{extraido}".strip()

    if not texto or len(texto) < 10:
        flash("O documento está vazio ou muito curto.", "error")
        return redirect(url_for("home"))

    out = build_output(texto)

    nova = Analise(
        titulo_resumo=out["tema_principal"],
        texto_original=texto,
        tipo_peca=out.get("meta", {}).get("tipo_peca_detectado", "Documento Jurídico")
    )
    db.session.add(nova)
    db.session.commit()

    return render_template(
        "resultado.html",
        out=out,
        texto=texto,
        now=datetime.now(),
        analise_id=nova.id
    )

@app.route("/resultado/<int:id>")
def resultado(id):
    analise = Analise.query.get_or_404(id)
    out = build_output(analise.texto_original)
    return render_template("resultado.html", out=out, texto=analise.texto_original, now=datetime.now(), analise_id=analise.id)

@app.route("/historico")
def historico():
    page = request.args.get("page", 1, type=int)
    analises = Analise.query.order_by(Analise.data_criacao.desc()).paginate(page=page, per_page=10)
    return render_template("historico.html", paginacao=analises)

@app.route("/excluir/<int:id>")
def excluir(id):
    analise = Analise.query.get_or_404(id)
    db.session.delete(analise)
    db.session.commit()
    flash("Análise removida.", "success")
    return redirect(url_for("home"))

@app.get("/biblioteca")
def biblioteca():
    return render_template("biblioteca.html", links=LIBRARY_LINKS)

@app.get("/glossario")
def glossario():
    return redirect(GLOSSARY_URL)

@app.get("/sobre")
def sobre():
    return render_template("sobre.html")

# Erros
@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
