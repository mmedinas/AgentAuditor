# -*- coding: utf-8 -*-
import streamlit as st
import os
import pandas as pd
import docx # pip install python-docx
import fitz # pip install pymupdf <= Biblioteca para PDF
from io import BytesIO
import re # Para extrair dados do resumo
import altair as alt # Para os gráficos
import time # Para timestamp no nome do arquivo

# Importando as ferramentas da LangChain para a API do Google
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# --- Funções para Ler os Arquivos (read_sp_file, read_analysis_files, read_drawing_files - Sem alteração) ---
# (Cole as funções da versão anterior aqui)
def read_sp_file(file):
    try:
        document = docx.Document(file); full_text = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows: [full_text.append(c.text) for c in row.cells]
        return '\n'.join(full_text)
    except Exception as e: st.session_state.read_error = f"Erro SP ({file.name}): {e}"; return ""

def read_analysis_files(files):
    all_content, file_names = [], []
    for file in files:
        try:
            content = ""; file_base_name = os.path.splitext(file.name)[0]
            if file.name.endswith('.csv'): df = pd.read_csv(BytesIO(file.getvalue()))
            elif file.name.endswith('.xlsx'): df = pd.read_excel(BytesIO(file.getvalue()))
            else: continue # Ignora tipos não suportados
            content = df.to_string(); file_names.append(file_base_name)
            all_content.append(f"--- CONTEÚDO DO ARQUIVO: {file_base_name} ---\n{content}\n")
        except Exception as e: st.session_state.read_error = f"Erro Lista ({file.name}): {e}"; return "", []
    return '\n'.join(all_content), file_names

def read_drawing_files(files):
    all_content, file_names = [], []
    for file in files:
        try:
            file_base_name = os.path.splitext(file.name)[0]; file_names.append(file_base_name)
            doc_text = f"--- CONTEÚDO DO DESENHO: {file_base_name} ---\n"
            pdf_document = fitz.open(stream=file.getvalue(), filetype="pdf")
            for i, page in enumerate(pdf_document): doc_text += f"\n--- Página {i+1} ---\n{page.get_text('text')}"
            pdf_document.close(); all_content.append(doc_text + "\n")
        except Exception as e: st.session_state.read_error = f"Erro Desenho ({file.name}): {e}"; return "", []
    return '\n'.join(all_content), file_names


# --- Prompts (MASTER_PROMPT_LISTS, MASTER_PROMPT_DRAWINGS - Sem alteração) ---
# (Cole os prompts da versão anterior aqui)
MASTER_PROMPT_LISTS = """
Sua **ÚNICA TAREFA** é comparar os itens físicos descritos na "Fonte da Verdade (SP)"...
... (Restante do prompt forte como na versão anterior, incluindo [RESUMO ESTRUTURADO PARA GRÁFICOS]) ...
"""
MASTER_PROMPT_DRAWINGS = """
Sua **ÚNICA TAREFA** é verificar se os itens físicos descritos na "Fonte da Verdade (SP)"...
... (Restante do prompt forte como na versão anterior, SEM [RESUMO ESTRUTURADO PARA GRÁFICOS]) ...
"""

# --- Funções Parse e Download (parse_summary_table, convert_df_to_csv - Sem alteração) ---
# (Cole as funções da versão anterior aqui)
def parse_summary_table(summary_section):
    pendencias = []
    pattern = r"\|\s*(FALTANTE|DISCREPANCIA_TECNICA|DISCREPANCIA_QUANTIDADE|IMPLICITO_FALTANTE)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
    lines = summary_section.strip().split('\n')
    if len(lines) > 2:
        data_lines = lines[2:]
        for line in data_lines:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                tipo = match.group(1).strip().upper(); lista_raw = match.group(2).strip(); detalhe = match.group(3).strip()
                if lista_raw.upper() == 'N/A': lista_clean = 'Geral/Não Encontrado'
                else:
                    lista_base = os.path.basename(lista_raw); lista_clean = os.path.splitext(lista_base)[0]
                    base_name_match = re.match(r"([a-zA-Z]+)(_|\d|-|$)", lista_clean)
                    if base_name_match: lista_clean = base_name_match.group(1)
                    else: lista_clean = lista_raw
                pendencias.append({"Tipo": tipo, "Lista": lista_clean, "Item": detalhe})
    return pd.DataFrame(pendencias)

@st.cache_data
def convert_df_to_csv(df):
    if df is None or df.empty: return "".encode('utf-8')
    return df.to_csv(index=False).encode('utf-8')

# --- Configuração da Página e CSS (Sem alteração) ---
st.set_page_config(page_title="Agente Auditor v7", layout="wide")
frame_css = """
<style>
.frame { border: 1px solid #e1e4e8; border-radius: 6px; padding: 1rem; background-color: #f6f8fa; box-shadow: 0 1px 3px rgba(0,0,0,0.05); margin-bottom: 1rem; min-height: 400px; }
.frame h3, .frame h4, .frame h5 { margin-top: 0; margin-bottom: 0.8rem; color: #0366d6; border-bottom: 1px solid #eaecef; padding-bottom: 0.3rem; }
.stFileUploader label { display: none;}
.st-emotion-cache-16txtl3 h3, .st-emotion-cache-16txtl3 h6 { padding-bottom: 0.5rem; border-bottom: 1px solid #eaecef; margin-bottom: 0.8rem; color: #0366d6;}
[data-testid="stSidebar"] { background-color: #F8F9FA; }
</style>"""
st.markdown(frame_css, unsafe_allow_html=True)

# --- Inicializa Session State (Sem alteração) ---
if 'read_error' not in st.session_state: st.session_state.read_error = None
if 'list_audit_results' not in st.session_state: st.session_state.list_audit_results = None
if 'drawing_audit_results' not in st.session_state: st.session_state.drawing_audit_results = None
if 'start_list_audit_clicked' not in st.session_state: st.session_state.start_list_audit_clicked = False
if 'start_drawing_audit_clicked' not in st.session_state: st.session_state.start_drawing_audit_clicked = False
if 'sp_file_uploader_key' not in st.session_state: st.session_state.sp_file_uploader_key = 0
if 'lm_uploader_key' not in st.session_state: st.session_state.lm_uploader_key = 0
if 'dwg_uploader_key' not in st.session_state: st.session_state.dwg_uploader_key = 0

# --- Header (Sem alteração) ---
st.markdown('<div class="frame">', unsafe_allow_html=True)
st.title("🤖✨ Agente Auditor de Projetos v7")
st.caption("Auditoria SP vs. Listas & SP vs. Desenhos | Gemini Cloud")
st.markdown('</div>', unsafe_allow_html=True)

# --- Sidebar (SEM CAMPO DE CHAVE API) ---
with st.sidebar:
    st.header("⚙️ Controles")

    # Apenas uma nota sobre a chave API
    st.subheader("Configuração API")
    st.caption("Este aplicativo usa a chave API configurada via 'Secrets' no ambiente de hospedagem (Streamlit Cloud).")
    google_api_key_from_secrets = os.getenv("GOOGLE_API_KEY") # Verifica se existe para validação posterior

    st.markdown("---")

    st.subheader("📄 Arquivos")
    st.markdown("###### Fonte da Verdade (SP)")
    sp_file = st.file_uploader("Upload .docx", type=["docx"], key=f"sp_uploader_{st.session_state.sp_file_uploader_key}", label_visibility="collapsed")

    st.markdown("###### Listas de Engenharia (LMM, LME, LMH)")
    analysis_files = st.file_uploader("Upload .xlsx, .csv", type=["xlsx", "csv"],
                                      accept_multiple_files=True, key=f"lm_uploader_{st.session_state.lm_uploader_key}", label_visibility="collapsed")

    st.markdown("###### Desenhos Técnicos (PDF)")
    drawing_files = st.file_uploader("Upload .pdf", type=["pdf"],
                                     accept_multiple_files=True, key=f"dwg_uploader_{st.session_state.dwg_uploader_key}", label_visibility="collapsed")
    st.markdown("---")

    st.subheader("🚀 Ações")
    # Botão Auditoria Listas
    if st.button("▶️ Auditar SP vs Listas", type="primary", use_container_width=True):
        st.session_state.start_list_audit_clicked = True
        st.session_state.start_drawing_audit_clicked = False

    # Botão Auditoria Desenhos
    if st.button("▶️ Auditar SP vs Desenhos", use_container_width=True):
        st.session_state.start_drawing_audit_clicked = True
        st.session_state.start_list_audit_clicked = False

    # Botão Limpar Tudo
    if st.button("🧹 Limpar Tudo", use_container_width=True):
         st.session_state.list_audit_results = None; st.session_state.drawing_audit_results = None
         st.session_state.read_error = None; st.session_state.start_list_audit_clicked = False
         st.session_state.start_drawing_audit_clicked = False
         st.session_state.sp_file_uploader_key += 1; st.session_state.lm_uploader_key += 1
         st.session_state.dwg_uploader_key += 1; st.rerun()

# --- Função Display Results (Sem alteração) ---
# (Cole a função da versão anterior aqui)
def display_results(audit_type, results):
    if results:
        summary_data, report_markdown = results
        st.markdown(f"#### {audit_type}: Relatório Detalhado")

        if report_markdown:
            st.download_button(
                 label=f"📄 Baixar Relatório ({audit_type})", data=report_markdown,
                 file_name=f"auditoria_{audit_type.lower()}_{time.strftime('%Y%m%d_%H%M%S')}.md",
                 mime='text/markdown')
        with st.expander(f"Clique para ver os detalhes ({audit_type})", expanded=False):
            st.markdown(report_markdown if report_markdown else f"*Nenhum relatório ({audit_type}) gerado.*")

        st.markdown("---")

        if audit_type == "Listas" and isinstance(summary_data, pd.DataFrame) and not summary_data.empty:
            st.markdown("#### Listas: Resumo Gráfico das Pendências")
            try:
                chart_data = summary_data.groupby(['Lista', 'Tipo']).size().reset_index(name='Contagem')
                csv_data = convert_df_to_csv(summary_data)
                st.download_button(label="💾 Baixar Tabela (CSV)", data=csv_data, file_name=f"pendencias_listas_{time.strftime('%Y%m%d_%H%M%S')}.csv", mime='text/csv')
                with st.expander("Dados agregados (`chart_data`)"): st.dataframe(chart_data)

                color_scale = alt.Scale(domain=['FALTANTE', 'DISCREPANCIA_TECNICA', 'DISCREPANCIA_QUANTIDADE', 'IMPLICITO_FALTANTE'], range=['#e45756', '#f58518', '#4c78a8', '#54a24b'])
                tooltip_config = ['Lista', 'Tipo', 'Contagem']
                chart = alt.Chart(chart_data).mark_bar().encode(
                    y=alt.Y('Lista', sort='-x', title='Lista / Origem'),
                    x=alt.X('Contagem', title='Nº de Pendências'),
                    color=alt.Color('Tipo', scale=color_scale, title='Tipo de Pendência'),
                    tooltip=tooltip_config
                ).properties(title='Pendências por Lista e Tipo').interactive()
                st.altair_chart(chart, use_container_width=True)
                st.caption("Use o menu (⋮) no canto do gráfico para salvar como PNG/SVG.")

            except Exception as chart_error: st.error(f"⚠️ Erro ao gerar o gráfico (Listas): {chart_error}")

        elif audit_type == "Listas": # Se for Listas mas não gerou gráfico
            if report_markdown and "nenhuma pendência encontrada" in report_markdown.lower(): st.info("✅ Nenhuma pendência encontrada (Listas).")
            else: st.warning("⚠️ Gráfico não gerado (dados de resumo ausentes/inválidos para Listas).")
        elif audit_type == "Desenhos": # Se for Desenhos
             if report_markdown: st.info(f"Verificação SP vs Desenhos concluída.")
             else: st.warning("⚠️ Relatório da verificação SP vs Desenhos vazio.")

# --- Área Principal (Resultados) ---
st.markdown('<div class="frame output-frame">', unsafe_allow_html=True) # Moldura única
st.header("📊 Status e Resultados da Auditoria")

# Define qual tipo de auditoria executar baseado no botão clicado
audit_to_run = None
if st.session_state.start_list_audit_clicked:
    audit_to_run = "Listas"
elif st.session_state.start_drawing_audit_clicked:
    audit_to_run = "Desenhos"

# Lógica principal de execução (roda se um botão foi clicado)
if audit_to_run:
    st.session_state.read_error = None # Limpa antes de tentar ler
    st.session_state.list_audit_results = None # Limpa resultados antigos
    st.session_state.drawing_audit_results = None

    # Validações
    valid = True
    # Valida APENAS a existência da chave no ambiente/secrets
    if not google_api_key_from_secrets:
        st.error("🔑 Chave API não configurada nos Segredos/Ambiente."); valid = False
    # Valida SP (necessário para ambos)
    current_sp_key = f"sp_uploader_{st.session_state.sp_file_uploader_key}"
    sp_file_obj = st.session_state.get(current_sp_key)
    if not sp_file_obj: st.error("📄 Arquivo SP?"); valid = False

    # Valida arquivos específicos da auditoria
    analysis_files_obj = None
    drawing_files_obj = None
    if audit_to_run == "Listas":
        current_lm_key = f"lm_uploader_{st.session_state.lm_uploader_key}"
        analysis_files_obj = st.session_state.get(current_lm_key)
        if not analysis_files_obj: st.error("📊 Listas Eng.?"); valid = False
    elif audit_to_run == "Desenhos":
        current_dwg_key = f"dwg_uploader_{st.session_state.dwg_uploader_key}"
        drawing_files_obj = st.session_state.get(current_dwg_key)
        if not drawing_files_obj: st.error("🖼️ Desenhos (PDF)?"); valid = False

    if valid:
        try:
            # Leitura do SP (comum a ambos)
            with st.spinner("⚙️ Lendo SP..."):
                sp_content = read_sp_file(sp_file_obj)

            if st.session_state.read_error or not sp_content:
                st.error(st.session_state.read_error or "⚠️ Conteúdo do SP vazio.")
            else:
                # Prepara variáveis específicas da auditoria
                target_content = ""
                target_prompt = ""
                target_result_key = ""
                spinner_msg = ""
                invoke_payload = {}

                if audit_to_run == "Listas":
                    with st.spinner("⚙️ Lendo Listas..."):
                        target_content, file_names = read_analysis_files(analysis_files_obj)
                    if st.session_state.read_error or not target_content:
                         st.error(st.session_state.read_error or "⚠️ Conteúdo das Listas vazio.")
                         raise ValueError("Falha na leitura das listas") # Interrompe execução
                    target_prompt = MASTER_PROMPT_LISTS
                    target_result_key = "list_audit_results"
                    spinner_msg = "🧠 Auditando SP vs Listas..."
                    invoke_payload = {"sp_content": sp_content, "analysis_content": target_content}

                elif audit_to_run == "Desenhos":
                    with st.spinner("⚙️ Lendo Desenhos (PDFs)..."):
                         target_content, file_names = read_drawing_files(drawing_files_obj)
                    if st.session_state.read_error or not target_content:
                         st.error(st.session_state.read_error or "⚠️ Conteúdo dos Desenhos vazio.")
                         raise ValueError("Falha na leitura dos desenhos") # Interrompe execução
                    target_prompt = MASTER_PROMPT_DRAWINGS
                    target_result_key = "drawing_audit_results"
                    spinner_msg = "🧠 Verificando SP vs Desenhos..."
                    invoke_payload = {"sp_content": sp_content, "drawing_content": target_content}


                st.success(f"✅ Arquivos SP e {audit_to_run} lidos!")
                MODEL_NAME = "gemini-flash-latest"
                llm = ChatGoogleGenerativeAI(model=MODEL_NAME) # Chave lida do ambiente
                prompt_template = ChatPromptTemplate.from_template(target_prompt)
                llm_chain = prompt_template | llm | StrOutputParser()

                # Execução
                with st.spinner(f"{spinner_msg} ({MODEL_NAME})..."):
                    char_count = len(sp_content or "") + len(target_content or "")
                    st.info(f"📡 Enviando {char_count:,} chars...")
                    raw_output = llm_chain.invoke(invoke_payload)

                    # Processa e guarda resultados
                    report_markdown = raw_output.strip(); summary_data = pd.DataFrame()
                    if audit_to_run == "Listas": # Processa resumo só para Listas
                        summary_marker = "[RESUMO ESTRUTURADO PARA GRÁFICOS]"
                        if summary_marker in raw_output:
                            parts = raw_output.split(summary_marker, 1); report_markdown = parts[0].strip()
                            summary_section = parts[1].strip()
                            if summary_section and summary_section.lower().strip() != "nenhuma":
                                summary_data = parse_summary_table(summary_section)

                    st.success(f"🎉 Auditoria {audit_to_run} Concluída!")
                    st.session_state[target_result_key] = (summary_data, report_markdown) # Salva no estado correto

        # Tratamento de Erros
        except Exception as e:
            error_message = f"❌ Erro ({audit_to_run}): {e}"
            # ... (Lógica de tratamento de erro como antes, adaptando a mensagem) ...
            if "API key" in str(e) or "credential" in str(e).lower(): error_message = f"🔑 Erro API Key ({audit_to_run}): Verifique os Secrets. {e}"
            elif "quota" in str(e).lower() or "limit" in str(e).lower() or "free tier" in str(e).lower(): error_message = f"🚦 Limite API ({audit_to_run}): {e}"
            elif "model" in str(e).lower() and "not found" in str(e).lower(): error_message = f"🤷 Modelo não encontrado ('{MODEL_NAME}')."
            st.error(error_message);
            # Limpa o resultado específico em caso de erro
            if audit_to_run == "Listas": st.session_state.list_audit_results = None
            elif audit_to_run == "Desenhos": st.session_state.drawing_audit_results = None

    # Limpa as flags dos botões DEPOIS de processar ou falhar
    st.session_state.start_list_audit_clicked = False
    st.session_state.start_drawing_audit_clicked = False
    # Força um rerun SE HOUVE SUCESSO OU ERRO para garantir a exibição correta
    if valid:
        st.rerun()

# Exibe os resultados (se existirem e nenhum botão foi clicado *agora*)
# Determina qual resultado exibir (prioriza o mais recente se ambos existissem por algum bug)
results_to_display = None
audit_type_to_display = None
if st.session_state.drawing_audit_results:
    results_to_display = st.session_state.drawing_audit_results
    audit_type_to_display = "Desenhos"
elif st.session_state.list_audit_results:
    results_to_display = st.session_state.list_audit_results
    audit_type_to_display = "Listas"

if results_to_display:
    display_results(audit_type_to_display, results_to_display)
# Mensagem inicial se nada foi processado ainda
elif not st.session_state.start_list_audit_clicked and not st.session_state.start_drawing_audit_clicked:
     st.info("Aguardando o upload dos arquivos e o início de uma auditoria...")


st.markdown('</div>', unsafe_allow_html=True) # Fecha moldura da área principal