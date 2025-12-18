# -*- coding: utf-8 -*-
import streamlit as st
import os
import pandas as pd
import docx # pip install python-docx
from io import BytesIO
import re # Para extrair dados do resumo
import altair as alt # Para os gráficos
import time # Para timestamp no nome do arquivo

# Importando as ferramentas da LangChain para a API do Google
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import HumanMessage, AIMessage # Para o histórico do chat

# --- Funções para Ler os Arquivos (Sem alteração) ---
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
        st.session_state.read_error = f"Erro ao ler SP ({file.name}): {e}"
        return ""

def read_analysis_files(files):
    all_content, file_names = [], []
    for file in files:
        try:
            content = ""
            file_base_name = os.path.splitext(file.name)[0] 
            if file.name.endswith('.csv'):
                df = pd.read_csv(BytesIO(file.getvalue()))
                content = df.to_string()
            elif file.name.endswith('.xlsx'):
                df = pd.read_excel(BytesIO(file.getvalue()))
                content = df.to_string()
            file_names.append(file_base_name)
            all_content.append(f"--- CONTEÚDO DO ARQUIVO: {file_base_name} ---\n{content}\n")
        except Exception as e:
            st.session_state.read_error = f"Erro ao ler Lista ({file.name}): {e}"
            return "", []
    return '\n'.join(all_content), file_names

# --- Prompts ---

MASTER_PROMPT_AUDIT = """
Sua **ÚNICA TAREFA** é comparar, ITEM POR ITEM, os componentes físicos descritos na "Fonte da Verdade (SP)" (tópicos 17-30) com as "Listas de Engenharia".

**PROIBIÇÕES:**
1. NÃO FAÇA RESUMOS.
2. NÃO OMITA A TABELA FINAL.

**FORMATO OBRIGATÓRIO:**
### PENDÊNCIAS - ITENS FALTANTES (SP vs Listas)
* **[Item da SP]:** Não encontrado nas Listas.

### PENDÊNCIAS - DISCREPÂNCIAS TÉCNICAS
* **[Item]:** SP diverge da Lista [NomeLista].
    * **SP:** [Especificação SP]
    * **Lista ([NomeLista]):** [Especificação Lista]

### PENDÊNCIAS - DISCREPÂNCIAS DE QUANTIDADE
* **[Item]:** Qtd na SP diverge da Lista [NomeLista].
    * **SP:** Qtd: [X]
    * **Lista ([NomeLista]):** Qtd: [Y]

---
**IMPORTANTE: APÓS o relatório Markdown, GERE ESTA TABELA OBRIGATORIAMENTE:**

[RESUMO ESTRUTURADO PARA GRÁFICOS]
| TipoPendencia           | NomeLista                 | DetalheItem                                        |
| :---------------------- | :------------------------ | :------------------------------------------------- |
| FALTANTE                | N/A                       | [Item da SP]                                       |
| DISCREPANCIA_TECNICA    | [NomeLista do Arquivo]    | [Item]                                             |
| DISCREPANCIA_QUANTIDADE | [NomeLista do Arquivo]    | [Item]                                             |
| IMPLICITO_FALTANTE      | N/A                       | [Item Implícito]                                   |
* (Repita uma linha para CADA pendência. Se não houver, escreva "Nenhuma".)
---

**DOCUMENTOS:**
--- SP ---
{sp_content}
--- LISTAS ---
{analysis_content}
"""

MASTER_PROMPT_EXTRACT = """
Sua **ÚNICA TAREFA** é extrair uma **Lista Mestra de Equipamentos** (BOM) da "Fonte da Verdade (SP)".
**PROIBIÇÕES:** NÃO FAÇA RESUMOS.

**FORMATO OBRIGATÓRIO:**
### Lista Mestra de Equipamentos
#### Categoria: Elétricos
* [Item 1] (Qtd: [Qtd], Especificação: [Espec.])

---
**IMPORTANTE: GERE A TABELA OBRIGATORIAMENTE PARA O CSV:**

[RESUMO ESTRUTURADO PARA EXTRAÇÃO]
| Categoria | Item_Consolidado | Quantidade | Especificacao_Resumida |
| :--- | :--- | :--- | :--- |
| Elétricos | [Item 1] | [Qtd] | [Espec.] |
* (Repita uma linha para CADA item.)
---
**DOCUMENTO:**
{sp_content}
"""

# --- NOVO PROMPT PARA O CHAT ---
MASTER_PROMPT_CHAT = """
Você é um assistente técnico especializado em projetos de engenharia de Unidades Móveis.
Você tem acesso aos documentos do projeto abaixo.
Sua tarefa é responder à pergunta do usuário APENAS com base nessas informações.
Se a informação não estiver nos documentos, diga "Não encontrei essa informação nos documentos fornecidos".

--- DOCUMENTOS DO PROJETO ---
FONTE DA VERDADE (SP):
{sp_content}

LISTAS DE ENGENHARIA / OUTROS:
{analysis_content}
-------------------------------

PERGUNTA DO USUÁRIO: {user_question}
"""

# --- Parsers e Conversão ---
def parse_summary_table(summary_section):
    pendencias = []
    pattern = r"\|\s*(FALTANTE|DISCREPANCIA_TECNICA|DISCREPANCIA_QUANTIDADE|IMPLICITO_FALTANTE)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
    lines = summary_section.strip().split('\n')
    if len(lines) > 2:
        data_lines = lines[2:]
        for line in data_lines:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                tipo = match.group(1).strip().upper()
                lista_raw = match.group(2).strip()
                detalhe = match.group(3).strip()
                if lista_raw.upper() == 'N/A': lista_clean = 'Geral/Não Encontrado'
                else:
                    lista_base = os.path.basename(lista_raw); lista_clean = os.path.splitext(lista_base)[0]
                    base_name_match = re.match(r"([a-zA-Z]+)(_|\d|-|$)", lista_clean)
                    if base_name_match: lista_clean = base_name_match.group(1)
                    else: lista_clean = lista_raw
                pendencias.append({"Tipo": tipo, "Lista": lista_clean, "Item": detalhe})
    return pd.DataFrame(pendencias)

def parse_extract_table(summary_section):
    itens = []
    pattern = r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
    lines = summary_section.strip().split('\n')
    if len(lines) > 2:
        data_lines = lines[2:] 
        for line in data_lines:
            match = re.search(pattern, line)
            if match and match.group(1).strip() != ":---":
                itens.append({
                    "Categoria": match.group(1).strip(), 
                    "Item_Consolidado": match.group(2).strip(),
                    "Quantidade": match.group(3).strip(), 
                    "Especificacao_Resumida": match.group(4).strip()
                })
    return pd.DataFrame(itens)

@st.cache_data
def convert_df_to_csv(df):
    if df is None or df.empty: return "".encode('utf-8')
    return df.to_csv(index=False, sep=';').encode('utf-8-sig')

# --- Configuração ---
st.set_page_config(page_title="Agente Auditor v6.5", layout="wide")
frame_css = """
<style>
.frame { border: 1px solid #e1e4e8; border-radius: 6px; padding: 1rem; background-color: #f6f8fa; box-shadow: 0 1px 3px rgba(0,0,0,0.05); margin-bottom: 1rem; min-height: 400px; }
.frame h3, .frame h4, .frame h5 { margin-top: 0; margin-bottom: 0.8rem; color: #0366d6; border-bottom: 1px solid #eaecef; padding-bottom: 0.3rem; }
.stFileUploader label { display: none; }
.st-emotion-cache-16txtl3 h3, .st-emotion-cache-16txtl3 h6 { padding-bottom: 0.5rem; border-bottom: 1px solid #eaecef; margin-bottom: 0.8rem; color: #0366d6; }
[data-testid="stSidebar"] { background-color: #F8F9FA; }
</style>
"""
st.markdown(frame_css, unsafe_allow_html=True)

# --- Session State ---
if 'read_error' not in st.session_state: st.session_state.read_error = None
if 'audit_results' not in st.session_state: st.session_state.audit_results = None
if 'extract_results' not in st.session_state: st.session_state.extract_results = None 
if 'start_audit_clicked' not in st.session_state: st.session_state.start_audit_clicked = False
if 'start_extract_clicked' not in st.session_state: st.session_state.start_extract_clicked = False 
if 'sp_file_uploader_key' not in st.session_state: st.session_state.sp_file_uploader_key = 0
if 'lm_uploader_key' not in st.session_state: st.session_state.lm_uploader_key = 0
# --- NOVOS STATES PARA CHAT ---
if 'chat_history' not in st.session_state: st.session_state.chat_history = []
if 'sp_text_cache' not in st.session_state: st.session_state.sp_text_cache = ""
if 'list_text_cache' not in st.session_state: st.session_state.list_text_cache = ""

# --- Header ---
st.title("🤖✨ Agente Auditor v6.5")
st.caption("Auditoria SP vs. Listas & Extração de Lista Mestra & Chat IA | Gemini Cloud")

# --- Sidebar ---
with st.sidebar:
    st.image("https://raw.githubusercontent.com/mmedinas/AgentAuditor/main/LOGO_MOBILE.png", width=150)
    st.header("⚙️ Controles")
    st.subheader("Chave API")
    google_api_key_from_secrets = os.getenv("GOOGLE_API_KEY")
    if google_api_key_from_secrets: st.caption("🔒 Chave API configurada.")
    else: st.caption("⚠️ Chave API NÃO configurada.")
    st.markdown("---")
    st.subheader("📄 Arquivos")
    st.markdown("###### Documento de Entrada (SP)")
    sp_file = st.file_uploader("Upload .docx", type=["docx"], key=f"sp_uploader_{st.session_state.sp_file_uploader_key}", label_visibility="collapsed")
    st.markdown("###### Listas de Engenharia")
    analysis_files = st.file_uploader("Upload .xlsx, .csv", type=["xlsx", "csv"], accept_multiple_files=True, key=f"lm_uploader_{st.session_state.lm_uploader_key}", label_visibility="collapsed")
    st.markdown("---")
    st.subheader("🚀 Ações")
    if st.button("▶️ Auditar SP vs Listas", type="primary", use_container_width=True):
        st.session_state.start_audit_clicked = True
        st.session_state.start_extract_clicked = False
        st.session_state.chat_history = [] # Limpa chat ao iniciar nova análise
        st.rerun() 
    if st.button("▶️ Extrair Lista Mestra da SP", use_container_width=True):
        st.session_state.start_audit_clicked = False 
        st.session_state.start_extract_clicked = True
        st.session_state.chat_history = [] # Limpa chat ao iniciar nova análise
        st.rerun() 
    if st.button("🧹 Limpar Tudo", use_container_width=True):
         st.session_state.audit_results = None; st.session_state.extract_results = None
         st.session_state.read_error = None
         st.session_state.start_audit_clicked = False; st.session_state.start_extract_clicked = False
         st.session_state.sp_file_uploader_key += 1; st.session_state.lm_uploader_key += 1
         st.session_state.chat_history = []
         st.session_state.sp_text_cache = ""; st.session_state.list_text_cache = ""
         st.rerun() 

# --- Área Principal ---
st.header("📊 Status e Resultados")

# Lógica AUDITORIA
if st.session_state.start_audit_clicked:
    st.session_state.read_error = None; st.session_state.audit_results = None; st.session_state.extract_results = None
    valid = True
    if not google_api_key_from_secrets: st.error("🔑 Chave API?"); valid = False
    current_sp_key = f"sp_uploader_{st.session_state.sp_file_uploader_key}"
    current_lm_key = f"lm_uploader_{st.session_state.lm_uploader_key}"
    sp_file_obj = st.session_state.get(current_sp_key)
    analysis_files_obj = st.session_state.get(current_lm_key)
    if not sp_file_obj: st.error("📄 Arquivo SP?"); valid = False
    if not analysis_files_obj: st.error("📊 Listas Eng.?"); valid = False

    if valid:
        try:
            with st.spinner("⚙️ Lendo arquivos..."):
                sp_content = read_sp_file(sp_file_obj)
                analysis_content, file_names = read_analysis_files(analysis_files_obj)
                # --- SALVA TEXTO NO CACHE PARA O CHAT ---
                st.session_state.sp_text_cache = sp_content
                st.session_state.list_text_cache = analysis_content
                
            if st.session_state.read_error: st.error(st.session_state.read_error)
            elif not sp_content or not analysis_content: st.warning("⚠️ Conteúdo vazio.")
            else:
                st.success(f"✅ Arquivos lidos!")
                MODEL_NAME = "gemini-flash-latest"
                llm = ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=0.0) 
                prompt_template = ChatPromptTemplate.from_template(MASTER_PROMPT_AUDIT) 
                llm_chain = prompt_template | llm | StrOutputParser()
                with st.spinner(f"🧠 Auditando ({MODEL_NAME})..."):
                    raw_output = llm_chain.invoke({"sp_content": sp_content, "analysis_content": analysis_content})
                    report_markdown = raw_output; summary_data = pd.DataFrame()
                    summary_marker = "[RESUMO ESTRUTURADO PARA GRÁFICOS]"
                    if summary_marker in raw_output:
                        parts = raw_output.split(summary_marker, 1); report_markdown = parts[0].strip()
                        summary_section = parts[1].strip()
                        if summary_section and summary_section.lower().strip() != "nenhuma":
                            summary_data = parse_summary_table(summary_section)
                    st.success("🎉 Auditoria Concluída!")
                    st.session_state.audit_results = (summary_data, report_markdown)
        except Exception as e:
            st.error(f"❌ Erro: {e}")
    st.session_state.start_audit_clicked = False
    if valid: st.rerun()

# Lógica EXTRAÇÃO
elif st.session_state.start_extract_clicked:
    st.session_state.read_error = None; st.session_state.audit_results = None; st.session_state.extract_results = None
    valid = True
    if not google_api_key_from_secrets: st.error("🔑 Chave API?"); valid = False
    current_sp_key = f"sp_uploader_{st.session_state.sp_file_uploader_key}"
    sp_file_obj = st.session_state.get(current_sp_key)
    if not sp_file_obj: st.error("📄 Arquivo SP?"); valid = False

    if valid:
        try:
            with st.spinner("⚙️ Lendo arquivo SP..."):
                sp_content = read_sp_file(sp_file_obj)
                # --- SALVA TEXTO NO CACHE PARA O CHAT ---
                st.session_state.sp_text_cache = sp_content
                st.session_state.list_text_cache = "(Nenhuma lista carregada para extração)"

            if st.session_state.read_error: st.error(st.session_state.read_error)
            elif not sp_content: st.warning("⚠️ Conteúdo da SP vazio.")
            else:
                st.success(f"✅ Arquivo SP lido!")
                MODEL_NAME = "gemini-flash-latest"
                llm = ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=0.0) 
                prompt_template = ChatPromptTemplate.from_template(MASTER_PROMPT_EXTRACT) 
                llm_chain = prompt_template | llm | StrOutputParser()
                with st.spinner(f"🧠 Extraindo Lista Mestra ({MODEL_NAME})..."):
                    raw_output = llm_chain.invoke({"sp_content": sp_content}) 
                    report_markdown = raw_output; summary_data = pd.DataFrame() 
                    summary_marker = "[RESUMO ESTRUTURADO PARA EXTRAÇÃO]" 
                    if summary_marker in raw_output:
                        parts = raw_output.split(summary_marker, 1); report_markdown = parts[0].strip()
                        summary_section = parts[1].strip()
                        if summary_section and summary_section.lower().strip() != "nenhuma":
                            summary_data = parse_extract_table(summary_section)
                    st.success("🎉 Extração Concluída!")
                    st.session_state.extract_results = (summary_data, report_markdown) 
        except Exception as e:
            st.error(f"❌ Erro: {e}")
    st.session_state.start_extract_clicked = False
    if valid: st.rerun()

# --- EXIBIÇÃO DE RESULTADOS E CHAT ---
active_results = st.session_state.audit_results or st.session_state.extract_results
audit_type = None
if st.session_state.audit_results: audit_type = "Auditoria"
elif st.session_state.extract_results: audit_type = "Extração da SP"

if active_results:
    summary_data, report_markdown = active_results
    st.markdown(f"#### {audit_type}: Relatório Detalhado")

    st.download_button(
         label=f"📄 Baixar Relatório (Markdown)",
         data=report_markdown if report_markdown else "Erro.",
         file_name=f"relatorio.md", mime='text/markdown',
     )
    
    if isinstance(summary_data, pd.DataFrame) and not summary_data.empty:
        csv_data = convert_df_to_csv(summary_data)
        st.download_button(label=f"💾 Baixar Tabela (CSV)", data=csv_data, file_name=f"tabela.csv", mime='text/csv')
    elif audit_type == "Extração da SP":
        st.warning("⚠️ Aviso: Tabela CSV não gerada. Veja o relatório de texto.")

    with st.expander(f"Ver Detalhes ({audit_type})", expanded=True):
        st.markdown(report_markdown if report_markdown else f"*Vazio.*")
    
    st.markdown("---")
    
    # Exibe Gráfico se for Auditoria
    if audit_type == "Auditoria" and isinstance(summary_data, pd.DataFrame) and not summary_data.empty:
        try:
            chart_data = summary_data.groupby(['Lista', 'Tipo']).size().reset_index(name='Contagem')
            chart = alt.Chart(chart_data).mark_bar().encode(
                y=alt.Y('Lista', sort='-x'), x='Contagem', color='Tipo', tooltip=['Lista', 'Tipo', 'Contagem']
            ).properties(title='Pendências').interactive()
            st.altair_chart(chart, use_container_width=True)
        except: pass

    # --- ÁREA DE CHAT TIRA-DÚVIDAS (NOVO) ---
    st.markdown("### 💬 Tire dúvidas sobre os documentos")
    st.caption("Faça perguntas sobre a SP ou as Listas carregadas (ex: 'Qual a marca do ar condicionado?', 'Onde fala sobre o piso?').")
    
    # Exibe histórico
    for msg in st.session_state.chat_history:
        if isinstance(msg, HumanMessage):
            with st.chat_message("user"): st.markdown(msg.content)
        elif isinstance(msg, AIMessage):
            with st.chat_message("assistant"): st.markdown(msg.content)

    # Input do Chat
    if user_question := st.chat_input("Digite sua pergunta sobre o projeto..."):
        # Adiciona pergunta ao histórico
        st.session_state.chat_history.append(HumanMessage(content=user_question))
        with st.chat_message("user"): st.markdown(user_question)

        # Processa resposta
        with st.chat_message("assistant"):
            with st.spinner("Analisando documentos..."):
                try:
                    MODEL_NAME = "gemini-flash-latest"
                    # Pode usar temperatura um pouco maior aqui para ser mais conversacional, ou 0.0 para precisão
                    llm_chat = ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=0.1) 
                    prompt_chat = ChatPromptTemplate.from_template(MASTER_PROMPT_CHAT)
                    chain_chat = prompt_chat | llm_chat | StrOutputParser()
                    
                    # Usa o cache de texto salvo anteriormente
                    response = chain_chat.invoke({
                        "sp_content": st.session_state.sp_text_cache,
                        "analysis_content": st.session_state.list_text_cache,
                        "user_question": user_question
                    })
                    st.markdown(response)
                    st.session_state.chat_history.append(AIMessage(content=response))
                except Exception as e:
                    st.error(f"Erro ao responder: {e}")

elif (not st.session_state.start_audit_clicked and 
      not st.session_state.start_extract_clicked):
     st.info("Aguardando início...")
