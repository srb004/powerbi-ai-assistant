import os

import streamlit as st
from dotenv import load_dotenv

from llm_agent import LLMAgent
from mcp_client import PowerBIMCPClient
from render import render_response

load_dotenv()

st.set_page_config(
    page_title="Power BI AI Assistant",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
[data-testid="stAppViewContainer"] { background: #0f1117; }
[data-testid="stSidebar"]          { background: #161b27; border-right: 1px solid #2a2f3d; }
.sidebar-title { font-size: 1.15rem; font-weight: 700; color: #e2e8f0; letter-spacing: .03em; margin-bottom: .1rem; }
.sidebar-sub   { font-size: .75rem; color: #8892a4; margin-bottom: 1rem; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 20px; font-size: .75rem; font-weight: 600; }
.badge-connected    { background: #1a3a2a; color: #4ade80; border: 1px solid #166534; }
.badge-disconnected { background: #2a1a1a; color: #f87171; border: 1px solid #7f1d1d; }
[data-testid="stChatMessage"] { border-radius: 12px; margin-bottom: .5rem; }
div.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #6366f1, #8b5cf6);
    border: none; border-radius: 8px; font-weight: 600;
}
div.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #818cf8, #a78bfa);
}
</style>
""",
    unsafe_allow_html=True,
)


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
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


with st.sidebar:
    st.markdown('<p class="sidebar-title">Power BI AI Assistant</p>', unsafe_allow_html=True)
    st.markdown('<p class="sidebar-sub">Semantic Model Explorer</p>', unsafe_allow_html=True)

    if not st.session_state.session_started:
        st.markdown("#### Step 1 — Load Workspaces")
        st.caption("Fetches workspaces via REST API using the service principal.")
        if st.button("Start", type="primary", use_container_width=True):
            with st.spinner("Authenticating…"):
                try:
                    client = PowerBIMCPClient(
                        tenant_id=os.getenv("TENANT_ID"),
                        client_id=os.getenv("CLIENT_ID"),
                        client_secret=os.getenv("CLIENT_SECRET"),
                        mcp_exe_path=os.getenv("MCP_EXE_PATH"),
                    )
                    st.session_state.client = client
                    with st.spinner("Fetching workspaces…"):
                        st.session_state.workspaces = client.list_workspaces()
                    st.session_state.session_started = True
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed: {e}")

    elif not st.session_state.connected:
        client = st.session_state.client
        st.markdown("#### Step 2 — Choose Workspace & Model")

        workspaces = st.session_state.workspaces
        if not workspaces:
            st.warning("No workspaces found. Check service principal permissions.")
        else:
            ws = st.selectbox(
                "Workspace",
                options=workspaces,
                index=(
                    workspaces.index(st.session_state.selected_workspace)
                    if st.session_state.selected_workspace in workspaces
                    else 0
                ),
                key="ws_select",
            )

            if ws != st.session_state.selected_workspace:
                st.session_state.selected_workspace = ws
                with st.spinner("Loading semantic models…"):
                    st.session_state.datasets = client.list_datasets(ws)
                st.rerun()

            datasets = st.session_state.datasets
            if not datasets:
                st.info("No semantic models found in this workspace.")
            else:
                ds = st.selectbox("Semantic Model", options=datasets, key="ds_select")
                st.session_state.selected_dataset = ds

                if st.button("Connect", type="primary", use_container_width=True):
                    with st.spinner("Starting MCP server…"):
                        try:
                            client.start_session()
                        except Exception as e:
                            st.error(f"MCP start failed: {e}")
                            st.stop()

                    with st.spinner(f"Connecting to **{ds}**…"):
                        try:
                            client.connect(ws, ds)
                        except Exception as e:
                            st.error(f"Connect failed: {e}")
                            st.stop()

                    with st.spinner("Building schema…"):
                        schema = client.build_schema()
                    st.session_state.schema = schema

                    with st.spinner("Generating DAX examples for this model…"):
                        cache_key = f"{ws}::{ds}"
                        if "dax_cache" not in st.session_state:
                            st.session_state.dax_cache = {}

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
        if st.button("Reset Session", use_container_width=True):
            if st.session_state.client:
                st.session_state.client.disconnect()
            for k, v in DEFAULTS.items():
                st.session_state[k] = v
            st.rerun()

    else:
        st.markdown(
            '<span class="badge badge-connected">● Connected</span>',
            unsafe_allow_html=True,
        )
        st.caption(f"**Workspace:** {st.session_state.selected_workspace}")
        st.caption(f"**Model:** {st.session_state.selected_dataset}")

        with st.expander("Schema", expanded=False):
            st.code(st.session_state.schema, language="text")

        if st.session_state.tool_log:
            with st.expander("Last tool calls", expanded=False):
                for i, step in enumerate(st.session_state.tool_log, 1):
                    st.markdown(f"**Step {i} — `{step['tool']}`**")
                    st.caption(step["result"][:300])

        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Clear chat", use_container_width=True):
                st.session_state.messages = []
                st.session_state.tool_log = []
                st.rerun()
        with col2:
            if st.button("Disconnect", use_container_width=True):
                st.session_state.client.disconnect()
                for k, v in DEFAULTS.items():
                    st.session_state[k] = v
                st.rerun()

st.markdown("## Power BI Semantic Model Assistant")

if not st.session_state.session_started:
    st.info("Click **Start** in the sidebar to begin.")
    examples = [
        ("Revenue", "What is the total revenue this year?"),
        ("Top Products", "Which product generates the most profit?"),
        ("Trends", "Show me order count by month"),
        ("Margins", "What is the profit margin percentage?"),
        ("Traffic", "Which UTM source drives the most conversions?"),
        ("Retention", "What is the repeat purchase rate by device?"),
    ]
    cols = st.columns(3)
    for i, (label, text) in enumerate(examples):
        with cols[i % 3]:
            st.markdown(
                f"""<div style='background:#1a1f2e;border-radius:10px;padding:12px;
                margin:4px 0;border:1px solid #2a2f3d'>
                <span style='font-size:.9rem;color:#94a3b8'>{label}</span><br>
                <span style='font-size:.85rem;color:#e2e8f0'>{text}</span></div>""",
                unsafe_allow_html=True,
            )
    st.stop()

elif not st.session_state.connected:
    st.info("Select a workspace and semantic model, then click **Connect**.")
    st.stop()

for turn in st.session_state.messages:
    with st.chat_message(turn["role"]):
        if turn["role"] == "assistant":
            render_response(turn["content"])
        else:
            st.markdown(turn["content"])

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
        status = st.status("Thinking…", expanded=True)

        answer, tool_log = st.session_state.agent.run(
            user_message=prompt,
            chat_history=st.session_state.messages[:-1],
            schema=st.session_state.schema,
            status_container=status,
        )

        st.session_state.tool_log = tool_log
        st.session_state.messages.append({"role": "assistant", "content": answer})
        render_response(answer)