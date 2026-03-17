# -*- coding: utf-8 -*-
import streamlit as st
import os
import pandas as pd
import docx
import json
from io import BytesIO
import altair as alt
import google.generativeai as genai

# --- FUNÇÕES DE UTILIDADE ---

def clean_dataframe(df):
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

# --- CONFIGURAÇÃO DA IA (DIRETO NO SDK DO GOOGLE) ---

def call_gemini_direct(system_prompt, user_content, api_key):
    try:
        genai.configure(api_key=api_key)
        # Usamos o Gemini 1.5 Flash (mais rápido e barato)
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=system_prompt
        )
        
        # Configuração para forçar resposta em JSON
        generation_config = {
            "temperature": 0,
            "top_p": 0.95,
            "top_k": 64,
            "max_output_tokens": 8192,
            "response_mime_type": "application/json",
        }
        
        response = model.generate_content(user_content, generation_config=generation_config)
        return response.text
    except Exception as e:
        raise Exception(f"Erro na Chamada do Google: {str(e)}")

# --- PROMPTS ---

SYSTEM_PROMPT_AUDIT = """Você é um auditor de engenharia. Compare a SP com as Listas.
Retorne um JSON exatamente com esta estrutura:
{
  "relatorio_markdown": "Texto do relatório aqui...",
  "pendencias": [
    {"Tipo": "FALTANTE", "Lista": "nome", "Item": "descrição"}
  ]
}"""

SYSTEM_PROMPT_EXTRACT = """Você é um especialista em BOM. Extraia itens da SP.
Retorne um JSON exatamente com esta estrutura:
{
  "relatorio_markdown": "Lista formatada...",
  "itens": [
    {"Categoria": "Mecânica", "Item": "nome", "Quantidade": "1", "Especificacao": "detalhe"}
  ]
}"""

# --- INTERFACE ---

st.set_page_config(page_title="Agente Auditor v7.6", layout="wide")

if 'chat_history' not in st.session_state: st.session_state.chat_history = []
if 'audit_data' not in st.session_state: st.session_state.audit_data = None
if 'sp_text' not in st.session_state: st.session_state.sp_text = ""
if 'list_text' not in st.session_state: st.session_state.list_text = ""

st.title("🤖 Agente Auditor v7.6 (Versão Estabilizada)")

with st.sidebar:
    st.header("Configurações")
    api_key = os.getenv("GOOGLE_API_KEY") or st.text_input("Google API Key:", type="password")
    
    st.divider()
    sp_file = st.file_uploader("1. SP (.docx)", type=["docx"])
    list_files = st.file_uploader("2. Listas (.xlsx, .csv)", type=["xlsx", "csv"], accept_multiple_files=True)
    
    st.divider()
    if st.button("🔍 Iniciar Auditoria", type="primary"):
        if api_key and sp_file and list_files:
            with st.spinner("Auditando..."):
                try:
                    st.session_state.sp_text = read_sp_file(sp_file)
                    st.session_state.list_text = read_analysis_files(list_files)
                    
                    res_json = call_gemini_direct(SYSTEM_PROMPT_AUDIT, f"SP: {st.session_state.sp_text}\n\nListas: {st.session_state.list_text}", api_key)
                    st.session_state.audit_data = json.loads(res_json)
                except Exception as e:
                    st.error(f"Erro: {e}")
        else: st.warning("Preencha tudo antes de começar.")

    if st.button("📋 Extrair Lista"):
        if api_key and sp_file:
            with st.spinner("Extraindo..."):
                try:
                    st.session_state.sp_text = read_sp_file(sp_file)
                    res_json = call_gemini_direct(SYSTEM_PROMPT_EXTRACT, f"Extraia da SP: {st.session_state.sp_text}", api_key)
                    st.session_state.audit_data = json.loads(res_json)
                except Exception as e:
                    st.error(f"Erro: {e}")

# --- RESULTADOS ---

if st.session_state.audit_data:
    data = st.session_state.audit_data
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Relatório")
        st.markdown(data.get("relatorio_markdown", ""))
    with c2:
        st.subheader("Dados")
        if "pendencias" in data:
            st.dataframe(pd.DataFrame(data["pendencias"]), use_container_width=True)
        if "itens" in data:
            st.dataframe(pd.DataFrame(data["itens"]), use_container_width=True)

    st.divider()
    st.subheader("💬 Chat Tira-Dúvidas")
    for m in st.session_state.chat_history:
        with st.chat_message(m["role"]): st.markdown(m["content"])

    if p := st.chat_input("Pergunte algo..."):
        st.session_state.chat_history.append({"role": "user", "content": p})
        with st.chat_message("user"): st.markdown(p)
        try:
            genai.configure(api_key=api_key)
            model_chat = genai.GenerativeModel('gemini-1.5-flash')
            ctx = f"Contexto SP: {st.session_state.sp_text[:4000]}"
            resp = model_chat.generate_content(f"{ctx}\n\nPergunta do usuário: {p}")
            with st.chat_message("assistant"): st.markdown(resp.text)
            st.session_state.chat_history.append({"role": "assistant", "content": resp.text})
        except Exception as e: st.error(e)
