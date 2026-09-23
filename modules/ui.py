import streamlit as st


def inject_styles():
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1500px;}
        [data-testid="stSidebar"] {border-right: 1px solid rgba(128,128,128,.16);}
        .hero {
            padding: 1.35rem 1.5rem;
            border: 1px solid rgba(128,128,128,.18);
            border-radius: 18px;
            margin-bottom: 1rem;
            background: linear-gradient(135deg, rgba(60,120,210,.08), rgba(90,180,150,.05));
        }
        .hero h1 {margin: 0; font-size: 2rem; line-height: 1.15;}
        .hero p {margin: .45rem 0 0 0; opacity: .75; font-size: 1rem;}
        .section-card {
            border: 1px solid rgba(128,128,128,.18);
            border-radius: 16px;
            padding: 1rem 1.1rem;
            margin-bottom: .8rem;
        }
        .status-ok, .status-ko, .status-warn {
            border-radius: 14px;
            padding: .9rem 1.05rem;
            margin: .4rem 0 1rem 0;
            font-weight: 650;
        }
        .status-ok {background: rgba(50,170,100,.11); border: 1px solid rgba(50,170,100,.30);}
        .status-ko {background: rgba(215,70,70,.10); border: 1px solid rgba(215,70,70,.28);}
        .status-warn {background: rgba(230,170,50,.10); border: 1px solid rgba(230,170,50,.28);}
        div[data-testid="stMetric"] {
            border: 1px solid rgba(128,128,128,.16);
            border-radius: 14px;
            padding: .8rem .9rem;
            background: rgba(128,128,128,.025);
        }
        div[data-testid="stMetric"] label {opacity: .72;}
        .small-muted {font-size: .86rem; opacity: .68;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def hero(title: str, subtitle: str):
    st.markdown(
        f'<div class="hero"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


def status_banner(valid: bool, blocking: int, warnings: int):
    if valid:
        text = f"✅ File validato — 0 errori bloccanti · {warnings} warning"
        css = "status-ok"
    else:
        text = f"❌ File non validato — {blocking} errori bloccanti · {warnings} warning"
        css = "status-ko"
    st.markdown(f'<div class="{css}">{text}</div>', unsafe_allow_html=True)
