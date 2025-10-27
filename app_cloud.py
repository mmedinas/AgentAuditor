# -*- coding: utf-8 -*-
import streamlit as st
import os
import pandas as pd
import docx # pip install python-docx
import fitz # pip install pymupdf <= Biblioteca para PDF
from io import BytesIO
import re
import altair as alt
import time

# Importando as ferramentas da LangChain para a API do Google
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# --- Funções para Ler os Arquivos ---

def read_sp_file(file):
    """Lê o conteúdo de um arquivo .docx (SP) e retorna como texto."""
    try:
        # ... (código como antes) ...
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
    """Lê múltiplos arquivos .csv ou .xlsx (Listas) e concatena."""
    # ... (código como antes) ...
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

# --- NOVA FUNÇÃO: Ler Desenhos (PDFs) ---
def read_drawing_files(files):
    """Lê múltiplos arquivos PDF (Desenhos) e extrai texto."""
    all_content, file_names = [], []
    for file in files:
        try:
            file_base_name = os.path.splitext(file.name)[0]
            file_names.append(file_base_name)
            doc_text = f"--- CONTEÚDO DO DESENHO: {file_base_name} ---\n"
            # Abre o PDF usando PyMuPDF (fitz)
            pdf_document = fitz.open(stream=file.getvalue(), filetype="pdf")
            page_num = 1
            for page in pdf_document:
                doc_text += f"\n--- Página {page_num} ---\n"
                doc_text += page.get_text("text") # Extrai texto simples
                page_num += 1
            pdf_document.close()
            all_content.append(doc_text + "\n")
        except Exception as e:
            st.session_state.read_error = f"Erro ao ler Desenho PDF ({file.name}): {e}"
            return "", [] # Retorna vazio se falhar

    return '\n'.join(all_content), file_names

# --- Prompts ---
# Prompt Mestre para Listas (sem alteração)
MASTER_PROMPT_LISTS = """
Sua **ÚNICA TAREFA** é comparar os itens físicos descritos na "Fonte da Verdade (SP)" (especificamente dos tópicos 17 ao 30) com os itens listados nas "Listas de Engenharia".
**NÃO GERE RELATÓRIOS DE KPIs...** Foque **EXCLUSIVAMENTE** na comparação de itens físicos.
**SIGA ESTAS REGRAS ESTRITAMENTE:**
1.  **EXTRAÇÃO (SP):** ... (como antes)
2.  **COMPARAÇÃO (Listas):** ... (como antes)
3.  **INFERÊNCIA (Implícitos):** ... (como antes)
4.  **RELATÓRIO DE PENDÊNCIAS:** ... (como antes)
**FORMATO OBRIGATÓRIO DO RELATÓRIO MARKDOWN:**
### PENDÊNCIAS - ITENS FALTANTES (SP vs Listas) ...
### PENDÊNCIAS - DISCREPÂNCIAS TÉCNICAS ...
### PENDÊNCIAS - DISCREPÂNCIAS DE QUANTIDADE ...
### ITENS IMPLÍCITOS FALTANTES ...
---
**IMPORTANTE: APÓS o relatório Markdown, adicione a seção de resumo estruturado:**
[RESUMO ESTRUTURADO PARA GRÁFICOS]
| TipoPendencia | NomeLista | DetalheItem |
... (como antes) ...
---
**DOCUMENTOS PARA ANÁLISE:**
[FONTE DA VERDADE (SP)]
{sp_content}
---
[LISTAS DE ENGENHARIA]
{analysis_content}
---
**INICIE O RELATÓRIO DE AUDITORIA DE PENDÊNCIAS ABAIXO:**
[RELATÓRIO DE AUDITORIA DE PENDÊNCIAS (Markdown)]
"""

# --- NOVO PROMPT MESTRE PARA DESENHOS ---
MASTER_PROMPT_DRAWINGS = """
Sua **ÚNICA TAREFA** é verificar se os itens físicos descritos na "Fonte da Verdade (SP)" (tópicos 17-30) estão mencionados ou representados no texto extraído dos "Desenhos Técnicos".

**NÃO compare quantidades ou especificações detalhadas.** Foque **EXCLUSIVAMENTE** na **presença** do item nos desenhos.

**SIGA ESTAS REGRAS ESTRITAMENTE:**
1.  **EXTRAÇÃO (SP):** Leia a SP (tópicos 17-30). Extraia os principais itens físicos (comprados/fabricados). Um item existe se '[X] Sim' ou se houver especificação/descrição/notas.
2.  **VERIFICAÇÃO (Desenhos):** Para cada item da SP, procure por menções (texto, legendas, títulos) nos "Desenhos Técnicos". Use o NOME DO ARQUIVO e NÚMERO DA PÁGINA (se disponíveis no texto extraído) ao reportar.
3.  **RELATÓRIO DE VERIFICAÇÃO:** Liste **TODOS** os itens extraídos da SP e indique se foram encontrados ou não nos desenhos. Use o formato Markdown abaixo.

**FORMATO OBRIGATÓRIO DO RELATÓRIO MARKDOWN:**
### Verificação SP vs Desenhos

* **[Item da SP 1]:** ✅ Encontrado (Mencionado no Desenho: [NomeDesenho], Página: [NumPagina])
* **[Item da SP 2]:** ❌ Não encontrado nos textos dos desenhos fornecidos.
* **[Item da SP 3]:** ✅ Encontrado (Mencionado no Desenho: [NomeDesenho])
    * (Repita para todos os itens da SP)

---
**IMPORTANTE:** Como esta é uma verificação de presença, **NÃO GERE** a seção [RESUMO ESTRUTURADO PARA GRÁFICOS].
---

**DOCUMENTOS PARA ANÁLISE:**

[FONTE DA VERDADE (SP)]
{sp_content}
---
[DESENHOS TÉCNICOS (Texto Extraído)]
{drawing_content}
---

**INICIE O RELATÓRIO DE VERIFICAÇÃO ABAIXO:**
[RELATÓRIO DE VERIFICAÇÃO (Markdown)]
"""

# --- Funções Parse e Download (convert_df_to_csv como antes) ---
# (parse_summary_table como antes)
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


@st.cache_data
def convert_df_to_csv(df):
    if df is None or df.empty: return "".encode('utf-8')
    return df.to_csv(index=False).encode('utf-8')

# --- Configuração da Página e CSS (como antes) ---
st.set_page_config(page_title="Agente Auditor v6", layout="wide")
frame_css = """ <style> ... </style> """ # (CSS como na versão anterior)
st.markdown(frame_css, unsafe_allow_html=True)

# --- Inicializa Session State (Adiciona chaves para desenhos) ---
if 'hide_input_cols' not in st.session_state: st.session_state.hide_input_cols = False
if 'read_error' not in st.session_state: st.session_state.read_error = None
if 'list_audit_results' not in st.session_state: st.session_state.list_audit_results = None # Renomeado
if 'drawing_audit_results' not in st.session_state: st.session_state.drawing_audit_results = None # Novo
if 'start_list_audit_clicked' not in st.session_state: st.session_state.start_list_audit_clicked = False # Renomeado
if 'start_drawing_audit_clicked' not in st.session_state: st.session_state.start_drawing_audit_clicked = False # Novo
if 'sp_file_uploader_key' not in st.session_state: st.session_state.sp_file_uploader_key = 0
if 'lm_uploader_key' not in st.session_state: st.session_state.lm_uploader_key = 0
if 'dwg_uploader_key' not in st.session_state: st.session_state.dwg_uploader_key = 0 # Novo

# --- Header (como antes) ---
st.markdown('<div class="frame">', unsafe_allow_html=True)
st.title("🤖✨ Agente Auditor de Projetos v6")
st.caption("Auditoria SP vs. Listas de Engenharia & SP vs. Desenhos | Gemini Cloud")
st.markdown('</div>', unsafe_allow_html=True)

# --- Sidebar (Inputs e Ações Atualizada) ---
with st.sidebar:
    st.header("⚙️ Controles")
    st.subheader("Chave API")
    google_api_key = st.text_input("Cole sua Chave API:", type="password", key="api_key_input", label_visibility="collapsed", placeholder="Chave API Google AI Studio")
    google_api_key_from_secrets = os.getenv("GOOGLE_API_KEY")
    # (Validação discreta da chave como antes)
    api_key_status = "⚠️ Chave API não encontrada."
    if google_api_key: api_key_status = "🔑 Chave API inserida."
    elif google_api_key_from_secrets: api_key_status = "🔒 Usando chave dos Segredos."
    st.caption(api_key_status)
    st.markdown("---")

    st.subheader("📄 Arquivos")
    st.markdown("###### Fonte da Verdade (SP)")
    sp_file = st.file_uploader("Upload .docx", type=["docx"], key=f"sp_uploader_{st.session_state.sp_file_uploader_key}", label_visibility="collapsed")

    st.markdown("###### Listas de Engenharia (LMM, LME, LMH)")
    analysis_files = st.file_uploader("Upload .xlsx, .csv", type=["xlsx", "csv"],
                                      accept_multiple_files=True, key=f"lm_uploader_{st.session_state.lm_uploader_key}", label_visibility="collapsed")

    # --- NOVO UPLOAD PARA DESENHOS ---
    st.markdown("###### Desenhos Técnicos (PDF)")
    drawing_files = st.file_uploader("Upload .pdf", type=["pdf"],
                                     accept_multiple_files=True, key=f"dwg_uploader_{st.session_state.dwg_uploader_key}", label_visibility="collapsed")
    st.markdown("---")

    st.subheader("🚀 Ações")
    # Botão Auditoria Listas
    if st.button("▶️ Auditar SP vs Listas", type="primary", use_container_width=True):
        st.session_state.start_list_audit_clicked = True
        st.session_state.start_drawing_audit_clicked = False # Garante que só um rode

    # --- NOVO BOTÃO AUDITORIA DESENHOS ---
    if st.button("▶️ Auditar SP vs Desenhos", use_container_width=True):
        st.session_state.start_drawing_audit_clicked = True
        st.session_state.start_list_audit_clicked = False # Garante que só um rode

    # Botão Limpar Tudo (Atualizado)
    if st.button("🧹 Limpar Tudo", use_container_width=True):
         st.session_state.list_audit_results = None
         st.session_state.drawing_audit_results = None # Limpa novo estado
         st.session_state.read_error = None
         st.session_state.start_list_audit_clicked = False
         st.session_state.start_drawing_audit_clicked = False
         st.session_state.sp_file_uploader_key += 1
         st.session_state.lm_uploader_key += 1
         st.session_state.dwg_uploader_key += 1 # Incrementa nova chave
         st.rerun()

# --- Área Principal (Resultados) ---
st.markdown('<div class="frame output-frame">', unsafe_allow_html=True)
st.header("📊 Status e Resultados da Auditoria")

# --- Lógica para Auditoria de Listas ---
if st.session_state.start_list_audit_clicked:
    st.session_state.read_error = None
    st.session_state.list_audit_results = None # Limpa resultado específico
    st.session_state.drawing_audit_results = None # Limpa o outro resultado também

    # Validações (Chave, SP, Listas)
    valid = True
    api_key_to_use = google_api_key or google_api_key_from_secrets
    if not api_key_to_use: st.error("🔑 Chave API?"); valid = False
    current_sp_key = f"sp_uploader_{st.session_state.sp_file_uploader_key}"
    current_lm_key = f"lm_uploader_{st.session_state.lm_uploader_key}"
    sp_file_obj = st.session_state.get(current_sp_key)
    analysis_files_obj = st.session_state.get(current_lm_key)
    if not sp_file_obj: st.error("📄 Arquivo SP?"); valid = False
    if not analysis_files_obj: st.error("📊 Listas Eng.?"); valid = False # Precisa das listas

    if valid:
        try:
            os.environ["GOOGLE_API_KEY"] = api_key_to_use
            with st.spinner("⚙️ Lendo SP e Listas..."):
                sp_content = read_sp_file(sp_file_obj)
                analysis_content, file_names = read_analysis_files(analysis_files_obj)

            if st.session_state.read_error: st.error(st.session_state.read_error)
            elif not sp_content or not analysis_content: st.warning("⚠️ Conteúdo vazio.")
            else:
                st.success(f"✅ Arquivos SP e Listas lidos!")
                MODEL_NAME = "gemini-flash-latest"
                llm = ChatGoogleGenerativeAI(model=MODEL_NAME)
                prompt_template = ChatPromptTemplate.from_template(MASTER_PROMPT_LISTS) # USA PROMPT DE LISTAS
                llm_chain = prompt_template | llm | StrOutputParser()

                with st.spinner(f"🧠 Auditando SP vs Listas ({MODEL_NAME})..."):
                    # ... (chamada invoke como antes, mas usando analysis_content) ...
                    char_count = len(sp_content or "") + len(analysis_content or "")
                    st.info(f"📡 Enviando {char_count:,} chars...")
                    raw_output = llm_chain.invoke({"sp_content": sp_content, "analysis_content": analysis_content})

                    # Processa e guarda resultados DE LISTAS
                    report_markdown = raw_output; summary_data = pd.DataFrame()
                    summary_marker = "[RESUMO ESTRUTURADO PARA GRÁFICOS]"
                    if summary_marker in raw_output:
                        parts = raw_output.split(summary_marker, 1); report_markdown = parts[0].strip()
                        summary_section = parts[1].strip()
                        if summary_section and summary_section.lower().strip() != "nenhuma":
                            summary_data = parse_summary_table(summary_section)
                    st.success("🎉 Auditoria SP vs Listas Concluída!")
                    st.session_state.list_audit_results = (summary_data, report_markdown) # Salva no estado correto

        except Exception as e:
            # (Tratamento de Erros como antes)
            error_message = f"❌ Erro (Listas): {e}"; ... ; st.error(error_message); st.session_state.list_audit_results = None

    st.session_state.start_list_audit_clicked = False # Reseta flag
    st.rerun() # Mostra resultados

# --- NOVA Lógica para Auditoria de Desenhos ---
elif st.session_state.start_drawing_audit_clicked:
    st.session_state.read_error = None
    st.session_state.list_audit_results = None # Limpa o outro resultado
    st.session_state.drawing_audit_results = None # Limpa resultado específico

    # Validações (Chave, SP, Desenhos)
    valid = True
    api_key_to_use = google_api_key or google_api_key_from_secrets
    if not api_key_to_use: st.error("🔑 Chave API?"); valid = False
    current_sp_key = f"sp_uploader_{st.session_state.sp_file_uploader_key}"
    current_dwg_key = f"dwg_uploader_{st.session_state.dwg_uploader_key}" # Usa chave de desenho
    sp_file_obj = st.session_state.get(current_sp_key)
    drawing_files_obj = st.session_state.get(current_dwg_key) # Pega arquivos de desenho
    if not sp_file_obj: st.error("📄 Arquivo SP?"); valid = False
    if not drawing_files_obj: st.error("🖼️ Desenhos (PDF)?"); valid = False # Precisa dos desenhos

    if valid:
        try:
            os.environ["GOOGLE_API_KEY"] = api_key_to_use
            with st.spinner("⚙️ Lendo SP e Desenhos (PDFs)..."):
                sp_content = read_sp_file(sp_file_obj)
                drawing_content, file_names = read_drawing_files(drawing_files_obj) # Usa nova função

            if st.session_state.read_error: st.error(st.session_state.read_error)
            elif not sp_content or not drawing_content: st.warning("⚠️ Conteúdo vazio.")
            else:
                st.success(f"✅ Arquivos SP e Desenhos lidos!")
                MODEL_NAME = "gemini-flash-latest"
                llm = ChatGoogleGenerativeAI(model=MODEL_NAME)
                prompt_template = ChatPromptTemplate.from_template(MASTER_PROMPT_DRAWINGS) # USA PROMPT DE DESENHOS
                llm_chain = prompt_template | llm | StrOutputParser()

                with st.spinner(f"🧠 Verificando SP vs Desenhos ({MODEL_NAME})..."):
                    # Passa drawing_content para o prompt
                    char_count = len(sp_content or "") + len(drawing_content or "")
                    st.info(f"📡 Enviando {char_count:,} chars...")
                    raw_output = llm_chain.invoke({"sp_content": sp_content, "drawing_content": drawing_content})

                    # Processa e guarda resultados DE DESENHOS
                    # Prompt de desenhos não pede resumo estruturado, só markdown
                    report_markdown = raw_output.strip()
                    summary_data = pd.DataFrame() # Sem dados para gráfico nesta auditoria

                    st.success("🎉 Verificação SP vs Desenhos Concluída!")
                    # Salva no estado correto (sem summary_data)
                    st.session_state.drawing_audit_results = (summary_data, report_markdown)

        except Exception as e:
            # (Tratamento de Erros similar)
            error_message = f"❌ Erro (Desenhos): {e}"; ... ; st.error(error_message); st.session_state.drawing_audit_results = None

    st.session_state.start_drawing_audit_clicked = False # Reseta flag
    st.rerun() # Mostra resultados

# --- Exibição de Resultados (Mostra o último que foi gerado) ---
# Verifica qual resultado existe e exibe
active_results = st.session_state.list_audit_results or st.session_state.drawing_audit_results
audit_type = "Listas" if st.session_state.list_audit_results else "Desenhos" if st.session_state.drawing_audit_results else None

if active_results:
    summary_data, report_markdown = active_results
    st.markdown(f"#### {audit_type}: Relatório Detalhado")

    # Botão de Download (sempre disponível se houver relatório)
    if report_markdown:
        st.download_button(
             label=f"📄 Baixar Relatório ({audit_type})",
             data=report_markdown,
             file_name=f"auditoria_{audit_type.lower()}_{time.strftime('%Y%m%d_%H%M%S')}.md",
             mime='text/markdown',
         )
    with st.expander(f"Clique para ver os detalhes da auditoria ({audit_type})", expanded=False):
        st.markdown(report_markdown if report_markdown else f"*Nenhum relatório ({audit_type}) gerado.*")

    st.markdown("---")

    # Exibe Gráfico SOMENTE se for auditoria de Listas e tiver dados
    if audit_type == "Listas" and isinstance(summary_data, pd.DataFrame) and not summary_data.empty:
        st.markdown("#### Listas: Resumo Gráfico das Pendências")
        try:
            # ... (código do gráfico e download CSV como antes) ...
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

        except Exception as chart_error:
             st.error(f"⚠️ Erro ao gerar o gráfico (Listas): {chart_error}")

    # Mensagem se auditoria de Listas não gerou gráfico
    elif audit_type == "Listas" and (not isinstance(summary_data, pd.DataFrame) or summary_data.empty):
         if report_markdown and "nenhuma pendência encontrada" in report_markdown.lower(): st.info("✅ Nenhuma pendência encontrada (Listas).")
         else: st.warning("⚠️ Gráfico não gerado (dados de resumo ausentes/inválidos para Listas).")
    # Mensagem para auditoria de Desenhos (que não tem gráfico)
    elif audit_type == "Desenhos":
         if report_markdown and "nenhum item foi encontrado" not in report_markdown.lower(): st.info("Verificação SP vs Desenhos concluída. Veja detalhes acima.")
         elif report_markdown: st.info("✅ Verificação SP vs Desenhos concluída (Nenhum item encontrado?). Veja detalhes.")
         else: st.warning("⚠️ Relatório da verificação SP vs Desenhos vazio.")


# Mensagem inicial se nada foi processado ainda
elif not st.session_state.start_list_audit_clicked and not st.session_state.start_drawing_audit_clicked:
     st.info("Aguardando o upload dos arquivos e o início de uma auditoria...")

st.markdown('</div>', unsafe_allow_html=True) # Fecha moldura da área principal