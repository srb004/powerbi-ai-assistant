import os
import streamlit as st
from dotenv import load_dotenv
from llm_agent import LLMAgent
from mcp_client import PowerBIMCPClient
from render import render_response

load_dotenv()

st.set_page_config(
    page_title="PowerBI AI Assistant",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap');

* { box-sizing: border-box; }
html, body, [data-testid="stAppViewContainer"] {
    font-family: 'Inter', sans-serif;
    background: #f8f9fc;
    color: #1a1f2e;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: #ffffff !important;
    border-right: 1px solid #e2e6ef;
}
[data-testid="stSidebar"] > div:first-child { padding: 1.5rem 1rem; }

/* ── Main area ── */
[data-testid="stAppViewContainer"] > .main { background: #f8f9fc; }
.block-container {
    padding-top: 0 !important;
    padding-bottom: 90px !important;
    max-width: 860px !important;
    margin: 0 auto;
}

/* ── Sticky header ── */
.sticky-header {
    position: sticky;
    top: 0;
    z-index: 100;
    background: #f8f9fc;
    border-bottom: 1px solid #e2e6ef;
    padding: 0.65rem 0 0.75rem;
    margin-bottom: 1rem;
}

/* ── Buttons ── */
div.stButton > button {
    font-family: 'Inter', sans-serif;
    font-weight: 500;
    font-size: 0.85rem;
    border-radius: 8px;
    border: 1px solid #e2e6ef;
    background: #ffffff;
    color: #64748b;
    transition: all 0.2s;
}
div.stButton > button:hover {
    border-color: #6366f1;
    color: #6366f1;
    background: #f5f3ff;
}
div.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
    border: none;
    color: #fff;
    font-weight: 600;
    letter-spacing: 0.02em;
}
div.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
    box-shadow: 0 4px 14px rgba(99,102,241,0.35);
}

/* ── Selectbox ── */
[data-testid="stSelectbox"] > div > div {
    background: #ffffff !important;
    border: 1px solid #e2e6ef !important;
    border-radius: 8px !important;
    color: #1a1f2e !important;
}

/* ── Chat messages ── */
[data-testid="stChatMessage"] {
    background: transparent !important;
    border: none !important;
    padding: 0.4rem 0 !important;
}

/* ── Status widget ── */
[data-testid="stStatusWidget"] {
    background: #ffffff !important;
    border: 1px solid #e2e6ef !important;
    border-radius: 10px !important;
}

/* ── Expanders ── */
[data-testid="stExpander"] {
    background: #ffffff !important;
    border: 1px solid #e2e6ef !important;
    border-radius: 8px !important;
}

/* ── Code blocks ── */
[data-testid="stCodeBlock"] {
    background: #f1f5f9 !important;
    border: 1px solid #e2e6ef !important;
    border-radius: 8px !important;
}

/* ── Dataframe ── */
[data-testid="stDataFrame"] {
    border: 1px solid #e2e6ef !important;
    border-radius: 10px !important;
    overflow: hidden !important;
}

/* ── Chat input — fixed bottom ── */
[data-testid="stBottom"] {
    background: linear-gradient(180deg, rgba(248,249,252,0) 0%, #f8f9fc 30%) !important;
    border-top: none !important;
    padding: 16px 0 18px !important;
}
[data-testid="stChatInput"] {
    max-width: 820px !important;
    margin: 0 auto !important;
    padding: 0 8px !important;
}
[data-testid="stChatInput"] > div {
    background: #ffffff !important;
    border: 1.5px solid #e2e6ef !important;
    border-radius: 28px !important;
    box-shadow: 0 4px 16px rgba(15, 23, 42, 0.06),
                0 1px 3px rgba(15, 23, 42, 0.04) !important;
    transition: border-color 0.2s, box-shadow 0.2s !important;
}
[data-testid="stChatInput"] > div:focus-within {
    border-color: #6366f1 !important;
    box-shadow: 0 0 0 4px rgba(99,102,241,0.10),
                0 4px 16px rgba(99,102,241,0.12) !important;
}
[data-testid="stChatInput"] textarea {
    background: transparent !important;
    border: none !important;
    color: #1a1f2e !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 0.95rem !important;
    padding: 14px 18px !important;
    box-shadow: none !important;
}
[data-testid="stChatInput"] textarea:focus {
    outline: none !important;
    box-shadow: none !important;
}
[data-testid="stChatInput"] textarea::placeholder {
    color: #94a3b8 !important;
}
/* Send button — gradient to match brand */
[data-testid="stChatInputSubmitButton"] {
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%) !important;
    border: none !important;
    color: #ffffff !important;
    border-radius: 50% !important;
    width: 36px !important;
    height: 36px !important;
    margin-right: 6px !important;
    transition: transform 0.15s, box-shadow 0.15s !important;
}
[data-testid="stChatInputSubmitButton"]:hover:not(:disabled) {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(99,102,241,0.35) !important;
}
[data-testid="stChatInputSubmitButton"]:disabled {
    background: #e2e6ef !important;
    color: #94a3b8 !important;
}
[data-testid="stChatInputSubmitButton"] svg {
    fill: currentColor !important;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: #f8f9fc; }
::-webkit-scrollbar-thumb { background: #e2e6ef; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #cbd5e1; }

hr { border-color: #e2e6ef !important; margin: 1rem 0 !important; }

/* ── Sidebar footer (Powered by LatentView Analytics Ltd.) ── */
[data-testid="stSidebar"] > div:first-child {
    display: flex !important;
    flex-direction: column !important;
    min-height: 100vh !important;
}
.lv-footer {
    margin-top: auto !important;
    padding: 1.25rem 0 0.5rem !important;
    border-top: 1px solid #e2e6ef !important;
    text-align: center !important;
    font-size: 0.72rem !important;
    color: #94a3b8 !important;
    letter-spacing: 0.02em !important;
}
.lv-footer .lv-brand {
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    font-weight: 600;
    letter-spacing: 0.04em;
}
</style>
""", unsafe_allow_html=True)

LV_FOOTER_HTML = """
<div class="lv-footer">
    Powered by <span class="lv-brand">LatentView Analytics Ltd.</span>
</div>
"""

# ------------------------------------------------------------------ #
#  Session state                                                      #
# ------------------------------------------------------------------ #

DEFAULTS = {
    "messages": [],
    "client": None,
    "agent": None,
    "session_started": False,
    "connected": False,
    "schema": "",
    "tool_log": [],
    "pending_prompt": None,
    "workspaces": [],
    "datasets": [],
    "selected_workspace": None,
    "selected_dataset": None,
    "dax_cache": {},
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ------------------------------------------------------------------ #
#  Sidebar                                                            #
# ------------------------------------------------------------------ #

with st.sidebar:
    st.markdown("""
    <div style="margin-bottom:1.5rem">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">
            <div style="width:32px;height:32px;border-radius:8px;
                background:linear-gradient(135deg,#6366f1,#8b5cf6);
                display:flex;align-items:center;justify-content:center;flex-shrink:0">
                <svg xmlns="http://www.w3.org/2000/svg" width="17" height="17" viewBox="0 0 24 24"
                    fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/>
                    <line x1="6" y1="20" x2="6" y2="14"/>
                </svg>
            </div>
            <span style="font-size:1rem;font-weight:600;color:#1a1f2e;
                letter-spacing:0.02em">PowerBI AI Assistant</span>
        </div>
        <p style="font-size:0.72rem;color:#94a3b8;margin:0 0 0 42px">
            Semantic Model Explorer
        </p>
    </div>
    """, unsafe_allow_html=True)

    # ── Step 1: not started ──
    if not st.session_state.session_started:
        st.markdown("""
        <div style="background:#f5f3ff;border:1px solid #ddd6fe;border-radius:10px;
            padding:14px 16px;margin-bottom:1rem">
            <p style="font-size:0.78rem;color:#7c3aed;font-weight:600;margin:0 0 6px">STEP 1</p>
            <p style="font-size:0.88rem;color:#64748b;margin:0">
                Authenticate and load your Power BI workspaces to get started.
            </p>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Connect to Power BI", type="primary", use_container_width=True):
            with st.spinner("Authenticating…"):
                try:
                    client = PowerBIMCPClient(
                        tenant_id=os.getenv("TENANT_ID"),
                        client_id=os.getenv("CLIENT_ID"),
                        client_secret=os.getenv("CLIENT_SECRET"),
                    )
                    st.session_state.client = client
                    with st.spinner("Fetching workspaces…"):
                        st.session_state.workspaces = client.list_workspaces()
                    st.session_state.session_started = True
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed: {e}")

    # ── Step 2: started, not connected ──
    elif not st.session_state.connected:
        client = st.session_state.client
        st.markdown("""
        <div style="background:#f5f3ff;border:1px solid #ddd6fe;border-radius:10px;
            padding:14px 16px;margin-bottom:1rem">
            <p style="font-size:0.78rem;color:#7c3aed;font-weight:600;margin:0 0 6px">STEP 2</p>
            <p style="font-size:0.88rem;color:#64748b;margin:0">
                Choose a workspace and semantic model to query.
            </p>
        </div>
        """, unsafe_allow_html=True)

        workspaces = st.session_state.workspaces
        if not workspaces:
            st.warning("No workspaces found. Check service principal permissions.")
        else:
            ws = st.selectbox("Workspace", options=workspaces,
                index=(workspaces.index(st.session_state.selected_workspace)
                       if st.session_state.selected_workspace in workspaces else 0),
                key="ws_select")

            if ws != st.session_state.selected_workspace:
                st.session_state.selected_workspace = ws
                with st.spinner("Loading models…"):
                    st.session_state.datasets = client.list_datasets(ws)
                st.rerun()

            datasets = st.session_state.datasets
            if not datasets:
                st.info("No semantic models found in this workspace.")
            else:
                ds = st.selectbox("Semantic Model", options=datasets, key="ds_select")
                st.session_state.selected_dataset = ds
                st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

                if st.button("Launch Assistant", type="primary", use_container_width=True):
                    with st.spinner("Starting MCP server…"):
                        try:
                            client.start_session()
                        except Exception as e:
                            st.error(f"MCP start failed: {e}")
                            st.stop()
                    with st.spinner(f"Connecting to {ds}…"):
                        try:
                            client.connect(ws, ds)
                        except Exception as e:
                            st.error(f"Connect failed: {e}")
                            st.stop()
                    with st.spinner("Loading schema…"):
                        schema = client.build_schema()
                    st.session_state.schema = schema
                    with st.spinner("Generating DAX patterns…"):
                        cache_key = f"{ws}::{ds}"
                        agent = LLMAgent(
                            endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
                            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
                            deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
                            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-05-01-preview"),
                            mcp_client=client,
                            schema=schema,
                            dax_cache=st.session_state.dax_cache,
                            cache_key=cache_key,
                        )
                    st.session_state.agent = agent
                    st.session_state.connected = True
                    st.rerun()

        st.divider()
        if st.button("↩ Reset", use_container_width=True):
            if st.session_state.client:
                st.session_state.client.disconnect()
            for k, v in DEFAULTS.items():
                st.session_state[k] = v
            st.rerun()

    # ── Step 3: connected ──
    else:
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,#f0fdf4,#dcfce7);
            border:1px solid #86efac;border-radius:10px;padding:14px 16px;margin-bottom:1rem">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
                <div style="width:8px;height:8px;border-radius:50%;background:#16a34a"></div>
                <span style="font-size:0.78rem;font-weight:600;color:#15803d">CONNECTED</span>
            </div>
            <p style="font-size:0.72rem;color:#64748b;margin:0 0 2px;font-weight:600;
                letter-spacing:0.05em">WORKSPACE</p>
            <p style="font-size:0.85rem;color:#374151;margin:0 0 8px;
                white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                {st.session_state.selected_workspace}
            </p>
            <p style="font-size:0.72rem;color:#64748b;margin:0 0 2px;font-weight:600;
                letter-spacing:0.05em">MODEL</p>
            <p style="font-size:0.85rem;color:#4f46e5;margin:0;font-weight:500;
                white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                {st.session_state.selected_dataset}
            </p>
        </div>
        """, unsafe_allow_html=True)

        with st.expander("📐 Schema", expanded=False):
            st.code(st.session_state.schema, language="text")

        if st.session_state.tool_log:
            with st.expander(f"Last query trace ({len(st.session_state.tool_log)} steps)", expanded=False):
                for i, step in enumerate(st.session_state.tool_log, 1):
                    st.markdown(f"**Step {i}** — `{step['tool']}`")
                    st.caption(step["result"][:300])

        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Clear", use_container_width=True):
                st.session_state.messages = []
                st.session_state.tool_log = []
                st.rerun()
        with col2:
            if st.button("Disconnect", use_container_width=True):
                st.session_state.client.disconnect()
                for k, v in DEFAULTS.items():
                    st.session_state[k] = v
                st.rerun()

        if not st.session_state.messages:
            st.divider()
            st.markdown(
                "<p style='font-size:0.75rem;color:#94a3b8;font-weight:600;"
                "letter-spacing:0.05em;margin-bottom:8px'>QUICK START</p>",
                unsafe_allow_html=True)
            for s in ["What is the total revenue?", "Top 5 products by profit",
                      "Monthly revenue trend", "YoY sales comparison"]:
                if st.button(s, use_container_width=True, key=f"suggest_{s}"):
                    st.session_state.pending_prompt = s
                    st.rerun()

    # Footer — shown in every sidebar state
    st.markdown(LV_FOOTER_HTML, unsafe_allow_html=True)

if not st.session_state.session_started:
    st.markdown("""
    <div class="sticky-header">
        <div style="display:flex;align-items:center;gap:10px">
            <div style="width:30px;height:30px;border-radius:7px;flex-shrink:0;
                background:linear-gradient(135deg,#6366f1,#8b5cf6);
                display:flex;align-items:center;justify-content:center">
                <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24"
                    fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/>
                    <line x1="6" y1="20" x2="6" y2="14"/>
                </svg>
            </div>
            <span style="font-size:0.95rem;font-weight:600;color:#1a1f2e">PowerBI AI Assistant</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="text-align:center;padding:2.5rem 0 2rem">
        <div style="display:inline-flex;align-items:center;justify-content:center;
            width:64px;height:64px;border-radius:16px;margin-bottom:1.25rem;
            background:linear-gradient(135deg,#6366f1,#8b5cf6)">
            <svg xmlns="http://www.w3.org/2000/svg" width="30" height="30" viewBox="0 0 24 24"
                fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/>
                <line x1="6" y1="20" x2="6" y2="14"/>
            </svg>
        </div>
        <h1 style="font-size:2rem;font-weight:600;color:#1a1f2e;margin:0 0 0.5rem;
            letter-spacing:-0.02em">Power BI AI Assistant</h1>
        <p style="font-size:1rem;color:#64748b;margin:0 0 2.5rem;max-width:480px;
            margin-left:auto;margin-right:auto">
            Ask questions about your semantic models in plain English.
            Powered by GPT-5-Mini and the Power BI MCP server.
        </p>
    </div>
    """, unsafe_allow_html=True)

    cols = st.columns(3)
    features = [
        ('<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
         "Instant Insights", "Natural language to DAX — no query writing needed."),
        ('<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
         "ReAct Loop", "Multi-step reasoning with live tool call visibility."),
        ('<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg>',
         "Schema-Aware", "Dynamic DAX patterns generated from your model."),
    ]
    for col, (icon_svg, title, desc) in zip(cols, features):
        with col:
            st.markdown(f"""
            <div style="background:#ffffff;border:1px solid #e2e6ef;border-radius:12px;
                padding:20px;text-align:center;height:150px;display:flex;
                flex-direction:column;align-items:center;justify-content:center;
                box-shadow:0 1px 3px rgba(0,0,0,0.06)">
                <div style="margin-bottom:10px">{icon_svg}</div>
                <p style="font-size:0.88rem;font-weight:600;color:#1a1f2e;margin:0 0 6px">{title}</p>
                <p style="font-size:0.78rem;color:#64748b;margin:0">{desc}</p>
            </div>
            """, unsafe_allow_html=True)
    st.stop()

elif not st.session_state.connected:
    # Sticky header
    st.markdown("""
    <div class="sticky-header">
        <div style="display:flex;align-items:center;gap:10px">
            <div style="width:30px;height:30px;border-radius:7px;flex-shrink:0;
                background:linear-gradient(135deg,#6366f1,#8b5cf6);
                display:flex;align-items:center;justify-content:center">
                <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24"
                    fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/>
                    <line x1="6" y1="20" x2="6" y2="14"/>
                </svg>
            </div>
            <span style="font-size:0.95rem;font-weight:600;color:#1a1f2e">PowerBI AI Assistant</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="text-align:center;padding:4rem 0">
        <p style="font-size:1.5rem;font-weight:500;color:#1a1f2e;margin-bottom:0.5rem">
            Choose your data source
        </p>
        <p style="color:#64748b;font-size:0.95rem;margin:0">
            Select a workspace and semantic model in the sidebar,
            then click <strong style="color:#6366f1">Launch Assistant</strong>.
        </p>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ------------------------------------------------------------------ #
#  Main area — connected / chat                                       #
# ------------------------------------------------------------------ #

# Sticky header with model name + connected status
st.markdown(f"""
<div class="sticky-header">
    <div style="display:flex;align-items:center;justify-content:space-between">
        <div style="display:flex;align-items:center;gap:10px">
            <div style="width:30px;height:30px;border-radius:7px;flex-shrink:0;
                background:linear-gradient(135deg,#6366f1,#8b5cf6);
                display:flex;align-items:center;justify-content:center">
                <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24"
                    fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/>
                    <line x1="6" y1="20" x2="6" y2="14"/>
                </svg>
            </div>
            <div>
                <span style="font-size:0.95rem;font-weight:600;color:#1a1f2e">
                    {st.session_state.selected_dataset}
                </span>
                <span style="font-size:0.75rem;color:#94a3b8;margin-left:8px">
                    {st.session_state.selected_workspace}
                </span>
            </div>
        </div>
        <div style="display:flex;align-items:center;gap:6px;
            background:#f0fdf4;border:1px solid #86efac;
            border-radius:20px;padding:4px 10px">
            <div style="width:6px;height:6px;border-radius:50%;background:#16a34a"></div>
            <span style="font-size:0.72rem;font-weight:600;color:#15803d">Connected</span>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# ── Empty state ──
if not st.session_state.messages:
    st.markdown("""
    <div style="text-align:center;padding:2.5rem 0 2rem">
        <p style="font-size:1.4rem;font-weight:500;color:#1a1f2e;margin:0 0 0.4rem">
            What would you like to know?
        </p>
        <p style="font-size:0.88rem;color:#64748b;margin:0">
            Ask anything about your data — revenue, trends, segments, YoY comparisons.
        </p>
    </div>
    """, unsafe_allow_html=True)

    examples = [
        ('<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>',
         "Revenue", "What is total revenue by month?"),
        ('<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>',
         "Top Products", "Which product has the highest profit margin?"),
        ('<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
         "Trends", "Show me order count over time"),
        ('<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>',
         "YoY", "Compare revenue this year vs last year"),
        ('<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
         "Segments", "Break down sales by customer segment"),
        ('<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-4.92"/></svg>',
         "Returns", "What is the return rate by product?"),
    ]
    cols = st.columns(3)
    for i, (icon_svg, label, text) in enumerate(examples):
        with cols[i % 3]:
            st.markdown(f"""
            <div style="background:#ffffff;border:1px solid #e2e6ef;border-radius:10px;
                padding:12px 14px;margin-bottom:4px;box-shadow:0 1px 2px rgba(0,0,0,0.04)">
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
                    {icon_svg}
                    <span style="font-size:0.78rem;color:#6366f1;font-weight:600">{label}</span>
                </div>
                <span style="font-size:0.8rem;color:#374151">{text}</span>
            </div>
            """, unsafe_allow_html=True)
            if st.button("Ask", key=f"ex_{i}", use_container_width=True):
                st.session_state.pending_prompt = text
                st.rerun()

# ── Chat history ──
for turn in st.session_state.messages:
    with st.chat_message(turn["role"]):
        if turn["role"] == "assistant":
            render_response(turn["content"])
        else:
            st.markdown(turn["content"])

# ── Input ──
if st.session_state.pending_prompt:
    prompt = st.session_state.pending_prompt
    st.session_state.pending_prompt = None
else:
    prompt = st.chat_input("Ask anything about your data…")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status = st.status("Analysing…", expanded=True)
        # Placeholder for streaming tokens — sits between the status widget
        # and the final rendered response. Cleared once the structured answer
        # is ready so it doesn't duplicate the formatted view.
        stream_placeholder = st.empty()
        answer, tool_log = st.session_state.agent.run(
            user_message=prompt,
            chat_history=st.session_state.messages[:-1],
            schema=st.session_state.schema,
            status_container=status,
            stream_placeholder=stream_placeholder,
        )
        stream_placeholder.empty()
        st.session_state.tool_log = tool_log
        st.session_state.messages.append({"role": "assistant", "content": answer})
        render_response(answer)