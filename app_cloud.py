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

# --- Funções para Ler os Arquivos (Sem alteração) ---

def read_sp_file(file):
    """Lê o conteúdo de um arquivo .docx (SP) e retorna como texto."""
    try:
        document = docx.Document(file)
        full_text = [para.text for para in document.paragraphs]
        # Adiciona texto das tabelas
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    full_text.append(cell.text)
        return '\n'.join(full_text)
    except Exception as e:
        # Define o erro no estado da sessão para ser exibido na área principal
        st.session_state.read_error = f"Erro ao ler SP ({file.name}): {e}"
        return "" # Retorna vazio em caso de erro

def read_analysis_files(files):
    """Lê múltiplos arquivos .csv ou .xlsx (Listas) e concatena."""
    all_content, file_names = [], []
    for file in files:
        try:
            content = ""
            # Usa o nome base sem extensão para referência interna e no prompt
            file_base_name = os.path.splitext(file.name)[0] 
            if file.name.endswith('.csv'):
                bytes_data = file.getvalue()
                df = pd.read_csv(BytesIO(bytes_data))
                content = df.to_string()
            elif file.name.endswith('.xlsx'):
                bytes_data = file.getvalue()
                # O 'openpyxl' deve estar instalado (pip install openpyxl)
                df = pd.read_excel(BytesIO(bytes_data))
                content = df.to_string()
            
            file_names.append(file_base_name)
            # Adiciona marcador com nome do arquivo no conteúdo enviado para a IA
            all_content.append(f"--- CONTEÚDO DO ARQUIVO: {file_base_name} ---\n{content}\n")
            
        except Exception as e:
            # Define o erro no estado da sessão
            st.session_state.read_error = f"Erro ao ler Lista ({file.name}): {e}"
            return "", [] # Retorna vazio se falhar em algum arquivo
            
    return '\n'.join(all_content), file_names # Retorna nomes também

# --- Prompt Mestre para Auditoria (Renomeado) ---
MASTER_PROMPT_AUDIT = """
Sua **ÚNICA TAREFA** é comparar os itens físicos descritos na "Fonte da Verdade (SP)" (tópicos 17-30) com os itens listados nas "Listas de Engenharia".
**NÃO GERE RELATÓRIOS DE KPIs...** Foque **EXCLUSIVAMENTE** na comparação dos itens físicos...
(Restante do prompt de auditoria como antes)
...
[RESUMO ESTRUTURADO PARA GRÁFICOS]
| TipoPendencia           | NomeLista                 | DetalheItem                                        |
... (como antes) ...
---
**DOCUMENTOS PARA ANÁLISE (NÃO INVENTE DADOS SE ELES NÃO FOREM FORNECIDOS):**
--- INÍCIO DA FONTE DA VERDADE (SP) ---
{sp_content}
--- FIM DA FONTE DA VERDADE (SP) ---
--- INÍCIO DAS LISTAS DE ENGENHARIA ---
{analysis_content}
--- FIM DAS LISTAS DE ENGENHARIA ---
**INICIE O RELATÓRIO DE AUDITORIA DE PENDÊNCIAS ABAIXO:**
[RELATÓRIO DE AUDITORIA DE PENDÊNCIAS (Markdown)]
"""

# --- NOVO PROMPT 2: Extração de Lista Mestra (ATUALIZADO PARA KEY-VALUE) ---
MASTER_PROMPT_EXTRACT = """
Sua **ÚNICA TAREFA** é atuar como um engenheiro de orçamentos e extrair uma **Lista Mestra de Equipamentos** (Bill of Materials - BOM) do documento "Fonte da Verdade (SP)".

**NÃO GERE RELATÓRIOS DE KPIs, CPI, SPI, etc.** Foque **EXCLUSIVAMENTE** na extração de itens físicos.

**REGRAS ESTRITAS:**
1.  **LEITURA COMPLETA:** Leia **TODO** o documento "FONTE DA VERDADE (SP)" (do início ao fim) para encontrar itens.
2.  **FONTES DE ITENS:** (Fonte A: Listas Finais, Fonte B: Texto Corrido).
3.  **CONSOLIDAÇÃO:** Crie uma **ÚNICA** lista mestra.
4.  **REMOVER DUPLICATAS:** Se um item da "Fonte B" (texto corrido) já estiver listado na "Fonte A" (listas finais), **NÃO** o repita.
5.  **RELATÓRIO DE EXTRAÇÃO:** Apresente a lista consolidada em formato Markdown. Tente agrupar por categoria.

**FORMATO OBRIGATÓRIO DO RELATÓRIO MARKDOWN:**
### Lista Mestra de Equipamentos (Extraída da SP)
#### Categoria: Elétricos
* [Item 1] (Qtd: [Qtd], Especificação: [Breve Espec.])
* [Item 2] (Qtd: [Qtd], Especificação: [Breve Espec.])
* (Continue para todas as categorias e itens encontrados)
---
**IMPORTANTE: APÓS o relatório Markdown, adicione a seção de resumo estruturado para EXPORTAÇÃO (FORMATO KEY-VALUE):**

O objetivo é criar uma tabela 'longa' (key-value) para análise em Excel (Tabela Dinâmica).
Para CADA item consolidado que você encontrou, crie múltiplas linhas na tabela abaixo:
1.  Uma linha para 'Categoria'.
2.  Uma linha para 'Quantidade'.
3.  Uma linha para CADA atributo técnico relevante (ex: 'Marca', 'Modelo', 'Potência', 'Cor', 'Material', 'Capacidade', etc.).

[RESUMO ESTRUTURADO PARA EXTRAÇÃO]
| Item_Consolidado | Atributo | Valor |
| :--- | :--- | :--- |
| Gerador | Categoria | Elétricos |
| Gerador | Quantidade | 1 |
| Gerador | Nível de Ruído | máx 67dB |
| Gerador | Regime | Intermitente (Prime) |
| Cadeira de Coleta | Categoria | Mobiliário |
| Cadeira de Coleta | Quantidade | 5 |
| Cadeira de Coleta | Movimento | Trendelenburg |
| Cadeira de Coleta | Capacidade | 130-250kg |
* (Repita este padrão para CADA item. Use 'N/A' se o valor não for informado.)
* Se não houver itens, escreva "Nenhum".
---
**DOCUMENTO PARA ANÁLISE (NÃO INVENTE DADOS SE ELE NÃO FOR FORNECIDO):**
--- INÍCIO DA FONTE DA VERDADE (SP) ---
{sp_content}
--- FIM DA FONTE DA VERDADE (SP) ---
**INICIE A LISTA MESTRA CONSOLIDADA ABAIXO:**
[LISTA MESTRA DE EQUIPAMENTOS (Markdown)]
"""


# --- Função para Parsear o Resumo Estruturado (Auditoria) ---
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
                if lista_raw.upper() == 'N/A':
                    lista_clean = 'Geral/Não Encontrado'
                else:
                    lista_base = os.path.basename(lista_raw); lista_clean = os.path.splitext(lista_base)[0]
                    base_name_match = re.match(r"([a-zA-Z]+)(_|\d|-|$)", lista_clean)
                    if base_name_match: lista_clean = base_name_match.group(1)
                    else: lista_clean = lista_raw
                pendencias.append({"Tipo": tipo, "Lista": lista_clean, "Item": detalhe})
    return pd.DataFrame(pendencias)

# --- FUNÇÃO DE PARSER ATUALIZADA (Para 3 colunas Key-Value) ---
def parse_extract_table(summary_section):
    """Parseia a tabela estruturada key-value da função de extração."""
    itens = []
    # Padrão para 3 colunas: Item_Consolidado | Atributo | Valor
    pattern = r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
    lines = summary_section.strip().split('\n')
    if len(lines) > 2:
        data_lines = lines[2:] # Pula header e linha de separação
        for line in data_lines:
            match = re.search(pattern, line)
            # Garante que não é uma linha vazia ou de formatação
            if match and match.group(1).strip() != ":---":
                item_consolidado = match.group(1).strip()
                atributo = match.group(2).strip()
                valor = match.group(3).strip()
                
                itens.append({
                    "Item_Consolidado": item_consolidado, 
                    "Atributo": atributo, 
                    "Valor": valor
                })
    return pd.DataFrame(itens)


# --- Função para converter DataFrame para CSV (Sem alteração) ---
@st.cache_data
def convert_df_to_csv(df):
    if df is None or df.empty:
        return "".encode('utf-8')
    return df.to_csv(index=False).encode('utf-8')

# --- Configuração da Página e CSS (Sem alteração) ---
st.set_page_config(page_title="Agente Auditor v6.2", layout="wide") # v6.2 agora

frame_css = """
<style>
/* Estilo base da moldura */
.frame { ... } 
/* ... (Resto do seu CSS como antes) ... */
</style>
"""
st.markdown(frame_css, unsafe_allow_html=True)

# --- Inicializa Session State (Sem alteração) ---
if 'read_error' not in st.session_state: st.session_state.read_error = None
if 'audit_results' not in st.session_state: st.session_state.audit_results = None
if 'start_audit_clicked' not in st.session_state: st.session_state.start_audit_clicked = False
if 'extract_results' not in st.session_state: st.session_state.extract_results = None 
if 'start_extract_clicked' not in st.session_state: st.session_state.start_extract_clicked = False 
if 'sp_file_uploader_key' not in st.session_state: st.session_state.sp_file_uploader_key = 0
if 'lm_uploader_key' not in st.session_state: st.session_state.lm_uploader_key = 0


# --- Header (Sem alteração) ---
st.title("🤖✨ Agente Auditor v6.2") # v6.2 agora
st.caption("Auditoria SP vs. Listas & Extração de Lista Mestra | Gemini Cloud")


# --- Sidebar (Sem alteração) ---
with st.sidebar:
    st.image("https://raw.githubusercontent.com/mmedinas/AgentAuditor/main/LOGO_MOBILE.png", width=150)
    st.header("📄 UPLOADS")
    st.markdown("###### Documento de Entrada (SP)")
    sp_file = st.file_uploader("Upload .docx", type=["docx"], key=f"sp_uploader_{st.session_state.sp_file_uploader_key}", label_visibility="collapsed")
    st.markdown("###### Listas de Engenharia")
    analysis_files = st.file_uploader("Upload .xlsx, .csv", type=["xlsx", "csv"],
                                      accept_multiple_files=True, key=f"lm_uploader_{st.session_state.lm_uploader_key}", label_visibility="collapsed")
    st.markdown("---")
    st.subheader("🚀 Ações")
    if st.button("▶️ Auditar SP vs Listas", type="primary", use_container_width=True):
        st.session_state.start_audit_clicked = True
        st.session_state.start_extract_clicked = False 
        st.rerun() 
    if st.button("▶️ Extrair Lista Mestra da SP", use_container_width=True):
        st.session_state.start_audit_clicked = False 
        st.session_state.start_extract_clicked = True
        st.rerun() 
    if st.button("🧹 Limpar Tudo", use_container_width=True):
         st.session_state.audit_results = None; st.session_state.extract_results = None
         st.session_state.read_error = None
         st.session_state.start_audit_clicked = False; st.session_state.start_extract_clicked = False
         st.session_state.sp_file_uploader_key += 1; st.session_state.lm_uploader_key += 1
         st.rerun() 
    st.subheader("Chave API")
    google_api_key_from_secrets = os.getenv("GOOGLE_API_KEY")
    if google_api_key_from_secrets:
         st.caption("🔒 Chave API configurada (via Segredos/Ambiente).")
    else:
         st.caption("⚠️ Chave API NÃO configurada nos Segredos/Ambiente.")
         st.caption("No Streamlit Cloud: vá em 'Settings > Secrets'.")
         st.caption("Localmente: defina a variável de ambiente GOOGLE_API_KEY.")

# --- Área Principal (Resultados) ---
# st.markdown('<div class="frame output-frame">', unsafe_allow_html=True) # Moldura (comentada)
st.header("📊 Status e Resultados da Auditoria")

# Lógica principal de execução (AUDITORIA)
if st.session_state.start_audit_clicked:
    st.session_state.read_error = None; st.session_state.audit_results = None; st.session_state.extract_results = None
    
    # Validações
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
            if st.session_state.read_error: st.error(st.session_state.read_error)
            elif not sp_content or not analysis_content: st.warning("⚠️ Conteúdo vazio.")
            else:
                st.success(f"✅ Arquivos lidos!")
                MODEL_NAME = "gemini-flash-latest"
                llm = ChatGoogleGenerativeAI(model=MODEL_NAME)
                prompt_template = ChatPromptTemplate.from_template(MASTER_PROMPT_AUDIT) # Usa prompt de auditoria
                llm_chain = prompt_template | llm | StrOutputParser()
                with st.spinner(f"🧠 Auditando ({MODEL_NAME})..."):
                    char_count = len(sp_content or "") + len(analysis_content or "")
                    st.info(f"📡 Enviando {char_count:,} caracteres para a API Gemini...")
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
            error_message = f"❌ Erro: {e}"; ... ; st.error(error_message);
    st.session_state.start_audit_clicked = False
    if valid: st.rerun()

# --- Lógica de (EXTRAÇÃO) ATUALIZADA ---
elif st.session_state.start_extract_clicked:
    st.session_state.read_error = None; st.session_state.audit_results = None; st.session_state.extract_results = None
    
    # Validações (só SP e Chave)
    valid = True
    if not google_api_key_from_secrets: st.error("🔑 Chave API?"); valid = False
    current_sp_key = f"sp_uploader_{st.session_state.sp_file_uploader_key}"
    sp_file_obj = st.session_state.get(current_sp_key)
    if not sp_file_obj: st.error("📄 Arquivo SP?"); valid = False

    if valid:
        try:
            with st.spinner("⚙️ Lendo arquivo SP..."):
                sp_content = read_sp_file(sp_file_obj)
            if st.session_state.read_error: st.error(st.session_state.read_error)
            elif not sp_content: st.warning("⚠️ Conteúdo da SP vazio.")
            else:
                st.success(f"✅ Arquivo SP lido!")
                MODEL_NAME = "gemini-flash-latest"
                llm = ChatGoogleGenerativeAI(model=MODEL_NAME)
                prompt_template = ChatPromptTemplate.from_template(MASTER_PROMPT_EXTRACT) # Usa prompt de extração
                llm_chain = prompt_template | llm | StrOutputParser()
                with st.spinner(f"🧠 Extraindo Lista Mestra ({MODEL_NAME})..."):
                    char_count = len(sp_content or "")
                    st.info(f"📡 Enviando {char_count:,} caracteres para a API Gemini...")
                    raw_output = llm_chain.invoke({"sp_content": sp_content}) 
                    
                    # --- LÓGICA DE PARSING ATUALIZADA ---
                    report_markdown = raw_output; summary_data = pd.DataFrame() # Começa com DF vazio
                    summary_marker = "[RESUMO ESTRUTURADO PARA EXTRAÇÃO]" # Novo marcador
                    
                    if summary_marker in raw_output:
                        parts = raw_output.split(summary_marker, 1); report_markdown = parts[0].strip()
                        summary_section = parts[1].strip()
                        if summary_section and summary_section.lower().strip() != "nenhuma":
                            # Usa o NOVO parser para criar o DataFrame
                            summary_data = parse_extract_table(summary_section) # <-- ALTERADO
                    
                    st.success("🎉 Extração da Lista Mestra Concluída!")
                    st.session_state.extract_results = (summary_data, report_markdown) # Salva AMBOS
        except Exception as e:
            error_message = f"❌ Erro: {e}"; ... ; st.error(error_message);
    st.session_state.start_extract_clicked = False
    if valid: st.rerun()


# --- Exibição de Resultados (ATUALIZADA) ---
active_results = st.session_state.audit_results or st.session_state.extract_results
audit_type = None
if st.session_state.audit_results: audit_type = "Auditoria"
elif st.session_state.
