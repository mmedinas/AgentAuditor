# -*- coding: utf-8 -*-
import streamlit as st
import os
import pandas as pd
import docx
import json
from io import BytesIO
import altair as alt

# Importando ferramentas da LangChain
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# --- FUNÇÕES DE UTILIDADE (Leitura e Limpeza) ---

def clean_dataframe(df):
    """Mantém apenas colunas úteis para economizar tokens e remove linhas vazias."""
    palavras_chave = ['item', 'descri', 'especifica', 'qtd', 'quant', 'unid', 'cod', 'part number']
    # Normaliza nomes das colunas
    df.columns = [str(c).lower().strip() for c in df.columns]
    # Filtra colunas que contenham as palavras chave
    cols_para_manter = [c for c in df.columns if any(p in c for p in palavras_chave)]
    
    if cols_para_manter:
        df = df[cols_para_manter]
    
    # Remove linhas onde todos os valores são nulos e limita o tamanho
    return df.dropna(how='all').head(500)

def read_sp_file(file):
    try:
        document = docx.Document(file)
        full_text = [para.text for para in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    full_text.append(cell.text)
        return '\n'.join(full_text)
    except Exception as e:
        st.error(f"Erro ao ler SP: {e}")
        return ""

def read_analysis_files(files):
    all_content = []
    for file in files:
        try:
            file_base_name = os.path.splitext(file.name)[0]
            if file.name.endswith('.csv'):
                # Tenta ler CSV com diferentes separadores
                df = pd.read_csv(BytesIO(file.getvalue()), sep=None, engine='python')
                df = clean_dataframe(df)
                all_content.append(f"--- LISTA: {file_base_name} ---\n{df.to_string(index=False)}\n")
            elif file.name.endswith('.xlsx'):
                excel_file = pd.ExcelFile(BytesIO(file.getvalue()))
                for sheet_name in excel_file.sheet_names:
                    df = pd.read_excel(excel_file, sheet_name=sheet_name)
                    df = clean_dataframe(df)
                    if not df.empty:
                        all_content.append(f"--- LISTA: {file_base_name} (Aba: {sheet_name}) ---\n{df.to_string(index=False)}\n")
        except Exception as e:
            st.error(f"Erro ao ler arquivo {file.name}: {e}")
    return '\n'.join(all_content)

# --- CONFIGURAÇÃO DOS MODELOS (IA) ---

# Usando o nome "gemini-1.5-flash" que é o padrão atual
def get_model(api_key, is_chat=False):
    model_name = "gemini-1.5-flash"
    if is_chat:
        # Para o chat, temperatura um pouco maior para ser mais natural
        return ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key, temperature=0.2)
    else:
        # Para auditoria, temperatura zero e formato JSON
        return ChatGoogleGenerativeAI(
            model=model_name, 
            google_api_key=api_key, 
            temperature=0,
            model_kwargs={"response_mime_type": "application/json"}
        )

# --- PROMPTS ---

SYSTEM_PROMPT_AUDIT = """Você é um auditor de engenharia experiente. 
Sua tarefa é comparar a SP (Solicitação de Projeto) com as Listas de Materiais de engenharia.
Analise descrições técnicas e quantidades.

Responda EXCLUSIVAMENTE em formato JSON com esta estrutura:
{{
  "relatorio_markdown": "Texto detalhado com os achados...",
  "pendencias": [
    {{"Tipo": "FALTANTE", "Lista": "nome", "Item": "descrição"}},
    {{"Tipo": "DISCREPANCIA_TECNICA", "Lista": "nome", "Item": "descrição"}},
    {{"Tipo": "DISCREPANCIA_QUANTIDADE", "Lista": "nome", "Item": "descrição"}}
  ]
}}"""

SYSTEM_PROMPT_EXTRACT = """Você é um especialista em extração de dados técnicos. 
Extraia todos os itens de materiais e equipamentos mencionados na SP.

Responda EXCLUSIVAMENTE em formato JSON:
{{
  "relatorio_markdown": "Texto com a lista mestra...",
  "itens": [
    {{"Categoria": "Civil/Elétrica/Mecânica", "Item": "nome", "Quantidade": "valor", "Especificacao": "detalhes"}}
  ]
}}"""

# --- INTERFACE ---

st.set_page_config(page_title="Agente Auditor v7.2", layout="wide")

if 'chat_history' not in st.session_state: st.session_state.chat_history = []
if 'audit_data' not in st.session_state: st.session_state.audit_data = None
if 'sp_text' not in st.session_state: st.session_state.sp_text = ""
if 'list_text' not in st.session_state: st.session_state.list_text = ""

st.title("🤖 Agente Auditor v7.2")

with st.sidebar:
    st.header("Configurações")
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        api_key = st.text_input("Insira sua Google API Key:", type="password")
    
    st.divider()
    sp_file = st.file_uploader("1. Documento Base (SP)", type=["docx"])
    list_files = st.file_uploader("2. Listas de Materiais", type=["xlsx", "csv"], accept_multiple_files=True)
    
    st.divider()
    if st.button("🔍 Iniciar Auditoria", type="primary"):
        if not api_key: st.error("Falta a chave API")
        elif not sp_file or not list_files: st.error("Suba os arquivos primeiro")
        else:
            with st.spinner("IA analisando..."):
                sp_content = read_sp_file(sp_file)
                list_content = read_analysis_files(list_files)
                st.session_state.sp_text = sp_content
                st.session_state.list_text = list_content
                
                try:
                    model = get_model(api_key)
                    prompt = ChatPromptTemplate.from_messages([
                        ("system", SYSTEM_PROMPT_AUDIT),
                        ("human", "SP: {sp}\n\nLISTAS: {listas}")
                    ])
                    chain = prompt | model | StrOutputParser()
                    res = chain.invoke({"sp": sp_content, "listas": list_content})
                    # Limpeza de segurança para o JSON
                    res_clean = res.replace("```json", "").replace("```", "").strip()
                    st.session_state.audit_data = json.loads(res_clean)
                except Exception as e:
                    st.error(f"Erro na IA: {e}")

    if st.button("📋 Extrair Lista Mestra"):
        if not api_key or not sp_file: st.error("Verifique a chave e o arquivo SP")
        else:
            with st.spinner("IA extraindo..."):
                sp_content = read_sp_file(sp_file)
                st.session_state.sp_text = sp_content
                try:
                    model = get_model(api_key)
                    prompt = ChatPromptTemplate.from_messages([
                        ("system", SYSTEM_PROMPT_EXTRACT),
                        ("human", "Extraia da SP: {sp}")
                    ])
                    chain = prompt | model | StrOutputParser()
                    res = chain.invoke({"sp": sp_content})
                    res_clean = res.replace("```json", "").replace("```", "").strip()
                    st.session_state.audit_data = json.loads(res_clean)
                except Exception as e:
                    st.error(f"Erro na IA: {e}")

# --- RESULTADOS ---

if st.session_state.audit_data:
    data = st.session_state.audit_data
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.subheader("Relatório")
        st.markdown(data.get("relatorio_markdown", ""))
    
    with col2:
        st.subheader("Dados Estruturados")
        if "pendencias" in data:
            df = pd.DataFrame(data["pendencias"])
            if not df.empty:
                st.dataframe(df, use_container_width=True)
                c = alt.Chart(df).mark_bar().encode(x='count()', y='Tipo', color='Tipo')
                st.altair_chart(c, use_container_width=True)
        
        if "itens" in data:
            df_itens = pd.DataFrame(data["itens"])
            st.dataframe(df_itens, use_container_width=True)

    st.divider()
    # Chat simplificado
    st.subheader("💬 Chat sobre o Projeto")
    for m in st.session_state.chat_history:
        with st.chat_message(m["role"]): st.markdown(m["content"])

    if ask := st.chat_input("Pergunte algo..."):
        st.session_state.chat_history.append({"role": "user", "content": ask})
        with st.chat_message("user"): st.markdown(ask)
        with st.chat_message("assistant"):
            try:
                chat_model = get_model(api_key, is_chat=True)
                ctx = f"CONTEXTO: {st.session_state.sp_text[:3000]}"
                p = ChatPromptTemplate.from_messages([
                    ("system", "Responda de forma técnica e objetiva."),
                    ("human", f"{ctx}\n\nPergunta: {ask}")
                ])
                resp = (p | chat_model | StrOutputParser()).invoke({})
                st.markdown(resp)
                st.session_state.chat_history.append({"role": "assistant", "content": resp})
            except Exception as e:
                st.error(f"Erro no chat: {e}")
