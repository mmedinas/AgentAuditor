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

# --- FUNÇÕES DE UTILIDADE ---

def clean_dataframe(df):
    """Limpa colunas irrelevantes para economizar tokens."""
    palavras_chave = ['item', 'descri', 'especifica', 'qtd', 'quant', 'unid', 'cod', 'part number']
    df.columns = [str(c).lower().strip() for c in df.columns]
    cols_para_manter = [c for c in df.columns if any(p in c for p in palavras_chave)]
    if cols_para_manter:
        df = df[cols_para_manter]
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

# --- MODELOS ---

def get_audit_model(api_key):
    # Usando a versão 'latest' para evitar o erro 404
    return ChatGoogleGenerativeAI(
        model="gemini-1.5-flash-latest", 
        google_api_key=api_key,
        temperature=0,
        model_kwargs={"response_mime_type": "application/json"}
    )

def get_chat_model(api_key):
    # Modelo 8B: Mais rápido e econômico para conversas
    return ChatGoogleGenerativeAI(
        model="gemini-1.5-flash-8b-latest", 
        google_api_key=api_key,
        temperature=0.2
    )

# --- PROMPTS ---

SYSTEM_PROMPT_AUDIT = """Você é um auditor de engenharia. Compare a SP com as Listas.
Responda APENAS em JSON:
{{
  "relatorio_markdown": "Relatório aqui...",
  "pendencias": [
    {{"Tipo": "FALTANTE", "Lista": "nome", "Item": "descrição"}}
  ]
}}"""

SYSTEM_PROMPT_EXTRACT = """Você é um especialista em BOM. Extraia itens da SP.
Responda APENAS em JSON:
{{
  "relatorio_markdown": "Lista aqui...",
  "itens": [
    {{"Categoria": "Mecânica", "Item": "nome", "Quantidade": "1", "Especificacao": "detalhe"}}
  ]
}}"""

# --- UI ---

st.set_page_config(page_title="Agente Auditor v7.3", layout="wide")

if 'chat_history' not in st.session_state: st.session_state.chat_history = []
if 'audit_data' not in st.session_state: st.session_state.audit_data = None
if 'sp_text' not in st.session_state: st.session_state.sp_text = ""
if 'list_text' not in st.session_state: st.session_state.list_text = ""

st.title("🤖 Agente Auditor v7.3")

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
        if not api_key or not sp_file or not list_files:
            st.warning("Verifique se a Chave API e os Arquivos foram carregados.")
        else:
            with st.spinner("IA Auditando..."):
                st.session_state.sp_text = read_sp_file(sp_file)
                st.session_state.list_text = read_analysis_files(list_files)
                try:
                    model = get_audit_model(api_key)
                    prompt = ChatPromptTemplate.from_messages([
                        ("system", SYSTEM_PROMPT_AUDIT),
                        ("human", "Analise:\nSP: {sp}\nListas: {listas}")
                    ])
                    chain = prompt | model | StrOutputParser()
                    res = chain.invoke({"sp": st.session_state.sp_text, "listas": st.session_state.list_text})
                    st.session_state.audit_data = json.loads(res.replace("```json", "").replace("```", ""))
                except Exception as e:
                    st.error(f"Erro na IA: {e}")

    if st.button("📋 Extrair Lista Mestra"):
        if not api_key or not sp_file:
            st.warning("Verifique a Chave API e o arquivo SP.")
        else:
            with st.spinner("IA Extraindo..."):
                st.session_state.sp_text = read_sp_file(sp_file)
                try:
                    model = get_audit_model(api_key)
                    prompt = ChatPromptTemplate.from_messages([
                        ("system", SYSTEM_PROMPT_EXTRACT),
                        ("human", "Extraia da SP: {sp}")
                    ])
                    chain = prompt | model | StrOutputParser()
                    res = chain.invoke({"sp": st.session_state.sp_text})
                    st.session_state.audit_data = json.loads(res.replace("```json", "").replace("```", ""))
                except Exception as e:
                    st.error(f"Erro na IA: {e}")

# --- RESULTADOS ---

if st.session_state.audit_data:
    data = st.session_state.audit_data
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Relatório")
        st.markdown(data.get("relatorio_markdown", ""))
    with c2:
        st.subheader("Tabela de Dados")
        if "pendencias" in data:
            df = pd.DataFrame(data["pendencias"])
            if not df.empty:
                st.dataframe(df, use_container_width=True)
                chart = alt.Chart(df).mark_bar().encode(x='count()', y='Tipo', color='Tipo')
                st.altair_chart(chart, use_container_width=True)
        if "itens" in data:
            df_itens = pd.DataFrame(data["itens"])
            st.dataframe(df_itens, use_container_width=True)

    st.divider()
    st.subheader("💬 Chat Tira-Dúvidas")
    for m in st.session_state.chat_history:
        with st.chat_message(m["role"]): st.markdown(m["content"])

    if p := st.chat_input("Pergunte algo sobre os documentos..."):
        st.session_state.chat_history.append({"role": "user", "content": p})
        with st.chat_message("user"): st.markdown(p)
        with st.chat_message("assistant"):
            try:
                chat_model = get_chat_model(api_key)
                ctx = f"SP: {st.session_state.sp_text[:3000]}\nListas: {st.session_state.list_text[:3000]}"
                prompt_chat = ChatPromptTemplate.from_messages([
                    ("system", "Você é um assistente técnico. Use o contexto para responder."),
                    ("human", f"{ctx}\n\nPergunta: {p}")
                ])
                resp = (prompt_chat | chat_model | StrOutputParser()).invoke({})
                st.markdown(resp)
                st.session_state.chat_history.append({"role": "assistant", "content": resp})
            except Exception as e:
                st.error(f"Erro no chat: {e}")
