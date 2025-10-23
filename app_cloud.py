import streamlit as st
import os
import pandas as pd
import docx
from io import BytesIO
import re # Para extrair dados do resumo
import altair as alt # Para os gráficos

# Importando as ferramentas da LangChain para a API do Google
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# --- Funções para Ler os Arquivos (Sem alteração) ---
# (read_sp_file e read_analysis_files como antes)
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
            file_base_name = os.path.splitext(file.name)[0] # Nome sem extensão
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


# --- O Prompt Mestre (Sem alteração) ---
MASTER_PROMPT = """
Você é um Engenheiro Sênior e Auditor de Projetos...
... (Todo o seu prompt mestre detalhado vai aqui, incluindo a seção [RESUMO ESTRUTURADO PARA GRÁFICICOs]) ...
""" # Fim do Master Prompt

# --- Função para Parsear o Resumo Estruturado (Sem alteração) ---
def parse_summary_table(summary_section):
    pendencias = []
    # Regex ajustado para ser mais flexível com espaços
    pattern = r"\|\s*(FALTANTE|DISCREPANCIA_TECNICA|DISCREPANCIA_QUANTIDADE|IMPLICITO_FALTANTE)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
    lines = summary_section.strip().split('\n')
    if len(lines) > 2:
        data_lines = lines[2:] # Pula header e linha de separação
        for line in data_lines:
            match = re.search(pattern, line, re.IGNORECASE) # Ignora case para N/A
            if match:
                tipo = match.group(1).strip().upper() # Garante tipo em maiúsculas
                lista_raw = match.group(2).strip()
                detalhe = match.group(3).strip()

                if lista_raw.upper() == 'N/A':
                    lista_clean = 'Geral/Não Encontrado'
                else:
                    # Tenta limpar nome do arquivo (remove path, extensão, etc.)
                    lista_base = os.path.basename(lista_raw)
                    lista_clean = os.path.splitext(lista_base)[0]
                    # Tenta pegar apenas a sigla inicial (LME, LMM, LMH) se aplicável
                    base_name_match = re.match(r"([a-zA-Z]+)(_|\d|-|$)", lista_clean)
                    if base_name_match:
                         lista_clean = base_name_match.group(1)

                pendencias.append({"Tipo": tipo, "Lista": lista_clean, "Item": detalhe})
    return pd.DataFrame(pendencias)


# --- Configuração da Página e CSS ---
st.set_page_config(page_title="Agente Auditor v4", layout="wide")

# CSS para molduras e controle de visibilidade
# Usaremos classes específicas para colunas de input e output
frame_css = """
<style>
/* Estilo base da moldura */
.frame {
    border: 1px solid #e1e4e8;
    border-radius: 6px;
    padding: 1rem;
    background-color: #f6f8fa;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    margin-bottom: 1rem;
}
/* Estilo dos títulos dentro das molduras */
.frame h3, .frame h5 {
    margin-top: 0;
    margin-bottom: 0.8rem;
    color: #0366d6;
    border-bottom: 1px solid #eaecef;
    padding-bottom: 0.3rem;
}
/* Garante que o container dentro da coluna use altura mínima */
.stVerticalBlock > div:has(> .frame) {
     min-height: 150px; /* Altura mínima para colunas de input/ações */
}
/* Classe específica para a moldura de resultados */
.output-frame {
     min-height: 300px; /* Altura mínima maior para a área de resultados */
}
</style>
"""
st.markdown(frame_css, unsafe_allow_html=True)

# --- Inicializa Session State ---
# 'hide_input_cols' controla a visibilidade
if 'hide_input_cols' not in st.session_state:
    st.session_state.hide_input_cols = False
if 'read_error' not in st.session_state:
    st.session_state.read_error = None
if 'audit_results' not in st.session_state:
    st.session_state.audit_results = None

# --- Header ---
# Pode ser um container com a classe "frame" se quiser moldura aqui também
st.markdown('<div class="frame">', unsafe_allow_html=True)
st.title("🤖✨ Agente Auditor de Projetos v4")
st.caption("Auditoria SP vs. Listas de Engenharia | Gemini Cloud")
st.markdown('</div>', unsafe_allow_html=True)


# --- Sidebar (Configuração e Controle de Visibilidade) ---
with st.sidebar:
    st.header("⚙️ Configuração")
    google_api_key = st.text_input("Cole sua Chave de API:", type="password", key="api_key_input")
    st.markdown("---")

    st.header("👁️ Visualização")
    # Botão para alternar a visibilidade
    button_label = "Expandir Resultados" if not st.session_state.hide_input_cols else "Mostrar Inputs"
    if st.button(button_label, use_container_width=True):
        st.session_state.hide_input_cols = not st.session_state.hide_input_cols
        st.rerun() # Força o rerender com o novo layout

    st.markdown("---")
    st.caption("🔑 Use chave gratuita do AI Studio para testes (limites aplicáveis).")


# --- Função para Exibir Resultados (Evita Duplicação) ---
def display_results():
    # Exibe resultados se existirem no estado da sessão
    if 'audit_results' in st.session_state and st.session_state.audit_results:
        summary_data, report_markdown = st.session_state.audit_results
        
        # Exibe Gráfico
        if not summary_data.empty:
            st.markdown("#### Resumo Gráfico das Pendências")
            chart_data = summary_data.groupby(['Lista', 'Tipo']).size().reset_index(name='Contagem')
            color_scale = alt.Scale(domain=['FALTANTE', 'DISCREPANCIA_TECNICA', 'DISCREPANCIA_QUANTIDADE', 'IMPLICITO_FALTANTE'],
                                    range=['#e45756', '#f58518', '#4c78a8', '#54a24b'])
            chart = alt.Chart(chart_data).mark_bar().encode(
                x=alt.X('Lista', sort='-y', title='Lista / Origem'),
                y=alt.Y('Contagem', title='Nº de Pendências'),
                color=alt.Color('Tipo', scale=color_scale, title='Tipo de Pendência'),
                tooltip=['Lista', 'Tipo', 'Contagem', alt.Tooltip('Item', title='Exemplo Item')]
            ).properties(
                title='Distribuição das Pendências por Lista e Tipo'
            ).interactive()
            st.altair_chart(chart, use_container_width=True)
        elif report_markdown and "nenhuma pendência encontrada" in report_markdown.lower():
            st.info("✅ Nenhuma pendência foi encontrada na auditoria.")
        else:
            st.warning("⚠️ Não foi possível gerar o gráfico ou o resumo estruturado. Verifique o relatório detalhado.")

        # Exibe Relatório Detalhado
        st.markdown("#### Relatório Detalhado")
        with st.expander("Clique para ver os detalhes da auditoria", expanded=st.session_state.hide_input_cols): # Expande automaticamente se colunas estiverem ocultas
            st.markdown(report_markdown if report_markdown else "Nenhum relatório gerado.")
            
    # Mensagem se não houver resultados e o botão não foi clicado agora
    elif 'start_audit_clicked' not in st.session_state or not st.session_state.start_audit_clicked:
         st.info("Aguardando o upload dos arquivos e o início da auditoria...")


# --- Layout Principal Condicional ---

if not st.session_state.hide_input_cols:
    # --- VISÃO PADRÃO (3 COLUNAS) ---
    col1, col2, col3 = st.columns([2, 1, 3]) # uploads(2), ações(1), resultados(3)

    # --- Coluna 1: Uploads ---
    with col1:
        st.markdown('<div class="frame">', unsafe_allow_html=True)
        st.subheader("📄 Arquivos")
        st.markdown("##### Fonte da Verdade (SP)")
        sp_file = st.file_uploader("Upload .docx", type=["docx"], key="sp_uploader_visible", label_visibility="collapsed")
        st.markdown("##### Listas de Engenharia")
        analysis_files = st.file_uploader("Upload .xlsx, .csv", type=["xlsx", "csv"], 
                                          accept_multiple_files=True, key="lm_uploader_visible", label_visibility="collapsed")
        st.markdown('</div>', unsafe_allow_html=True)

    # --- Coluna 2: Ações ---
    with col2:
        st.markdown('<div class="frame">', unsafe_allow_html=True)
        st.subheader("🚀 Ações")
        start_audit = st.button("Iniciar Auditoria", type="primary", use_container_width=True, key="start_button_visible")
        if start_audit:
            st.session_state.start_audit_clicked = True # Marca que o botão foi clicado

        if st.button("Limpar Tudo", use_container_width=True, key="clear_button_visible"):
             st.session_state.audit_results = None
             st.session_state.read_error = None
             # Idealmente, limparia os uploaders também, mas st.rerun é suficiente por enquanto
             st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # --- Coluna 3: Status e Resultados ---
    with col3:
        st.markdown('<div class="frame output-frame">', unsafe_allow_html=True) # Usa classe específica
        st.subheader("📊 Status e Resultados")

        # Lógica de execução da auditoria (só roda se botão foi clicado)
        if 'start_audit_clicked' in st.session_state and st.session_state.start_audit_clicked:
            st.session_state.read_error = None # Limpa antes de tentar ler
            st.session_state.audit_results = None # Limpa resultados antigos

            # Validações
            valid = True
            if not google_api_key: st.error("🔑 Chave API?"); valid = False
            # Usa as chaves corretas dos uploaders
            sp_file_obj = st.session_state.get('sp_uploader_visible')
            analysis_files_obj = st.session_state.get('lm_uploader_visible')
            if not sp_file_obj: st.error("📄 Arquivo SP?"); valid = False
            if not analysis_files_obj: st.error("📊 Listas Eng.?"); valid = False
                
            if valid:
                try:
                    os.environ["GOOGLE_API_KEY"] = google_api_key
                    # Leitura
                    with st.spinner("⚙️ Lendo..."):
                        sp_content = read_sp_file(sp_file_obj)
                        analysis_content, file_names = read_analysis_files(analysis_files_obj)
                    
                    if st.session_state.read_error: st.error(st.session_state.read_error)
                    elif not sp_content or not analysis_content: st.warning("⚠️ Conteúdo vazio.")
                    else:
                        st.success(f"✅ Arquivos lidos!")
                        MODEL_NAME = "gemini-flash-latest" 
                        llm = ChatGoogleGenerativeAI(model=MODEL_NAME)
                        prompt_template = ChatPromptTemplate.from_template(MASTER_PROMPT)
                        llm_chain = prompt_template | llm | StrOutputParser()

                        # Execução
                        with st.spinner(f"🧠 Auditando ({MODEL_NAME})..."):
                            char_count = len(sp_content or "") + len(analysis_content or "")
                            st.info(f"📡 Enviando {char_count:,} chars...")
                            raw_output = llm_chain.invoke({"sp_content": sp_content, "analysis_content": analysis_content})

                            # Processa e guarda resultados
                            report_markdown = raw_output
                            summary_data = pd.DataFrame()
                            summary_marker = "[RESUMO ESTRUTURADO PARA GRÁFICOS]"
                            if summary_marker in raw_output:
                                parts = raw_output.split(summary_marker, 1)
                                report_markdown = parts[0].strip()
                                summary_section = parts[1].strip()
                                if summary_section and summary_section.lower() != "nenhuma":
                                    summary_data = parse_summary_table(summary_section)
                            st.success("🎉 Auditoria Concluída!")
                            st.session_state.audit_results = (summary_data, report_markdown)

                # Tratamento de Erros
                except Exception as e:
                    error_message = f"❌ Erro: {e}"
                    if "API key" in str(e): error_message = f"🔑 Erro API Key: {e}"
                    elif "quota" in str(e).lower() or "limit" in str(e).lower(): error_message = f"🚦 Limite API: {e}"
                    elif "model" in str(e).lower() and "not found" in str(e).lower(): error_message = f"🤷 Modelo não encontrado ('{MODEL_NAME}')."
                    st.error(error_message)
                    st.session_state.audit_results = None 
            
            # Limpa o estado do botão após processar
            st.session_state.start_audit_clicked = False 

        # Chama a função para exibir os resultados (se houver)
        display_results()
        st.markdown('</div>', unsafe_allow_html=True) # Fecha moldura col3

else:
    # --- VISÃO EXPANDIDA (APENAS RESULTADOS) ---
    # A coluna de resultados ocupa a largura total
    st.markdown('<div class="frame output-frame">', unsafe_allow_html=True) # Usa classe específica
    st.subheader("📊 Resultados da Auditoria (Visão Expandida)")
    
    # Chama a função para exibir os resultados (que busca no session_state)
    display_results()
    
    st.markdown('</div>', unsafe_allow_html=True) # Fecha moldura

# --- (Fim do código principal) ---