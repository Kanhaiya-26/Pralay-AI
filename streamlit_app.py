"""PRALAY AI: Command Center (Streamlit). A standalone UI over the existing FastAPI backend; no logic of its own.
Run:  backend\\.venv\\Scripts\\streamlit.exe run streamlit_app.py --server.address 127.0.0.1"""
import pandas as pd
import pydeck as pdk
import requests
import streamlit as st

API = "http://127.0.0.1:8000"
CATEGORIES = {"Flood": "flood", "Fire": "fire", "Cyclone / storm": "cyclone_storm"}
# Fixed fingerprint so dashboard submissions count as their own device (fp_ + first 8 hex of sha256("streamlit-admin-dashboard")).
# The backend's IP fallback still applies: Streamlit submits from 127.0.0.1, the same IP as browsers on this laptop.
DASHBOARD_FINGERPRINT = "fp_4a2861f1"

st.set_page_config(page_title="PRALAY AI: Command Center", layout="wide")


def get(path, **params):
    try:
        r = requests.get(API + path, params=params, timeout=30)
        r.raise_for_status()
        return r.json(), None
    except requests.RequestException as e:
        return None, str(e)


with st.sidebar:
    st.title("PRALAY AI: Command Center")
    health, err = get("/api/health")
    if health:
        st.success("Connected to FastAPI Backend")
    else:
        st.error(f"Backend unreachable at {API}: {err}")
    st.caption("Grid is a CUSTOM / DEMO grid, not an official IMD/NDMA grid. UNVERIFIED means insufficient evidence, not false.")

tab_map, tab_submit, tab_chat, tab_cred = st.tabs(["Live GIS Map", "Submit Report", "Investigate AI", "Reporter Credibility"])

with tab_map:
    incidents, err = get("/api/incidents")
    reports, rerr = get("/api/reports", limit=200)
    if err or rerr:
        st.error(f"Could not load data: {err or rerr}")
    else:
        pts = pd.DataFrame([{"lat": i["center_lat"], "lon": i["center_lon"], "label": f"#{i['id']} {i['title']} ({i['effective_status']})",
                             "status": i["effective_status"]} for i in incidents if i.get("center_lat") is not None])
        if pts.empty:
            st.info("No located incidents yet.")
        else:
            colour = {"CORROBORATED": [34, 197, 94], "UNVERIFIED": [234, 179, 8], "CONTRADICTED": [239, 68, 68]}
            pts["color"] = pts["status"].map(lambda s: colour.get(s, [148, 163, 184]))
            st.pydeck_chart(pdk.Deck(
                initial_view_state=pdk.ViewState(latitude=22.5, longitude=79, zoom=3.8),
                layers=[pdk.Layer("ScatterplotLayer", pts, get_position="[lon, lat]", get_fill_color="color", get_radius=40000, pickable=True)],
                tooltip={"text": "{label}"}))
            st.caption("Incident centroids. Colour = verification status (green corroborated, yellow unverified, red contradicted).")
        st.subheader("Reports")
        st.dataframe(pd.DataFrame([{
            "ID": r["id"], "Category": r["category"], "Location": r.get("claimed_location_text"),
            "Confidence": r.get("report_confidence"), "Status": r["effective_status"],
            "Claim mismatch": bool(((r.get("assessment") or {}).get("fields") or {}).get("needs_review")),
        } for r in reports]), hide_index=True, use_container_width=True)

with tab_submit:
    with st.form("submit_report", clear_on_submit=False):
        up = st.file_uploader("Photo", type=["jpg", "jpeg", "png", "webp"])
        cat = st.selectbox("Disaster category", list(CATEGORIES))
        loc = st.text_input("Claimed location *", placeholder="e.g. Phase 7, Mohali, Punjab")
        desc = st.text_area("Description")
        sent = st.form_submit_button("Submit")
    if sent:
        if not loc.strip():
            st.error("Location is required to submit a report.")
        else:
            files = [("files", (up.name, up.getvalue(), up.type))] if up else []
            try:
                r = requests.post(API + "/api/reports", data={"category": CATEGORIES[cat], "claimed_location": loc, "description": desc,
                                                                  "device_fingerprint": DASHBOARD_FINGERPRINT},
                                  files=files, timeout=60)
                if r.status_code == 201:
                    st.success(f"Report #{r.json()['id']} received. It is processed automatically; the image check takes about 2-3 minutes.")
                else:
                    st.error(f"Rejected ({r.status_code}): {r.json().get('detail', r.text)}")
            except requests.RequestException as e:
                st.error(f"Backend unreachable: {e}")

with tab_chat:
    st.session_state.setdefault("chat", [])
    for role, text in st.session_state.chat:
        with st.chat_message(role):
            st.markdown(text)
    q = st.chat_input("Ask about disasters, incidents or evidence…")
    if q:
        st.session_state.chat.append(("user", q))
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            try:
                d = requests.post(API + "/api/chat", json={"query": q}, timeout=180).json()
                s = d.get("summary") or {}
                text = s.get("headline") or d.get("answer") or "No answer."
                if s.get("ai_phrased_by"):
                    text += f"\n\n_{s['ai_phrased_by']}_"
            except requests.RequestException as e:
                text = f"Backend unreachable: {e}"
            st.markdown(text)
        st.session_state.chat.append(("assistant", text))

with tab_cred:
    users, err = get("/api/users")
    if err:
        st.error(f"Could not load reporters: {err}")
    elif not users:
        st.info("No named reporters yet. Use 'Demo Reporter A' on submit.")
    else:
        df = pd.DataFrame(users).rename(columns={"reporter_name": "Reporter", "credibility_score": "Credibility", "reports_count": "Reports"})
        st.bar_chart(df.set_index("Reporter")["Credibility"])
        st.dataframe(df, hide_index=True, use_container_width=True)
        st.caption("Derived from each reporter's report outcomes (+5 corroborated, −10 contradicted or claim mismatch). Anonymous reports are not scored.")
