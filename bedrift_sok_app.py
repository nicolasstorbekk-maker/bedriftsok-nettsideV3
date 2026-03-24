import streamlit as st
import io
from pathlib import Path

from supabase import create_client, Client
from api import hent_kommunenummer, sok_alle_sider
from data_processing import bygg_dataframe
from constants import NAERINGSKODER


st.set_page_config(page_title="Bedriftssøk", layout="wide")


@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)


def init_auth() -> bool:
    """Handle authentication. Returns True if the user is logged in."""
    supabase = get_supabase()

    if "session" not in st.session_state:
        st.session_state["session"] = None

    # ── Handle password reset flow ─────────────────────────────
    token_hash = st.query_params.get("token_hash", "")
    if token_hash and st.query_params.get("type") == "recovery":

        _, col, _ = st.columns([1, 2, 1])
        with col:
            logo = Path(__file__).parent / "static" / "uldre.png"
            if logo.exists():
                st.image(str(logo), width=140)
            st.title("Sett nytt passord")

            with st.form("new_password_form"):
                new_password = st.text_input("Nytt passord", type="password")
                confirm_password = st.text_input("Bekreft passord", type="password")
                submitted = st.form_submit_button(
                    "Oppdater passord", width='stretch', type="primary"
                )

            if submitted:
                if not new_password or not confirm_password:
                    st.error("Fyll inn begge feltene.")
                elif new_password != confirm_password:
                    st.error("Passordene stemmer ikke overens.")
                else:
                    try:
                        supabase.auth.verify_otp(
                            {"token_hash": token_hash, "type": "recovery"}
                        )
                        supabase.auth.update_user({"password": new_password})
                        st.success("Passordet er oppdatert! Du kan nå logge inn.")
                        st.query_params.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Feil: {e}")
        return False

    # Validate existing session
    if st.session_state["session"] is not None:
        try:
            supabase.auth.get_user(st.session_state["session"].access_token)
            return True
        except Exception:
            st.session_state["session"] = None
            st.warning("Sesjonen din har utløpt. Logg inn på nytt.")

    # ── Login / Register UI ───────────────────────────────────
    _, col, _ = st.columns([1, 2, 1])
    with col:
        logo = Path(__file__).parent / "static" / "uldre.png"
        if logo.exists():
            st.image(str(logo), width=140)

        st.title("Bedriftssøk")

        with st.form("login_form"):
            email = st.text_input("E-post")
            password = st.text_input("Passord", type="password")
            submitted = st.form_submit_button(
                "Logg inn", width='stretch', type="primary"
            )

        if submitted:
            if not email or not password:
                st.error("Fyll inn e-post og passord.")
            else:
                try:
                    resp = supabase.auth.sign_in_with_password(
                        {"email": email.strip(), "password": password.strip()}
                    )
                    st.session_state["session"] = resp.session
                    st.rerun()
                except Exception as e:
                    msg = getattr(e, "message", str(e))
                    st.error(f"Innlogging feilet: {msg}")

        if "show_reset" not in st.session_state:
            st.session_state["show_reset"] = False

        if st.button("Glemt passord?", width='stretch'):
            st.session_state["show_reset"] = not st.session_state["show_reset"]

        if st.session_state["show_reset"]:
            with st.form("reset_form"):
                reset_email = st.text_input("Skriv inn e-postadressen din")
                reset_submitted = st.form_submit_button(
                    "Send tilbakestillingslenke", width='stretch'
                )

            if reset_submitted:
                if not reset_email:
                    st.error("Fyll inn e-postadressen din.")
                else:
                    try:
                        supabase.auth.reset_password_for_email(reset_email)
                        st.success("En tilbakestillingslenke er sendt til e-posten din.")
                        st.session_state["show_reset"] = False
                    except Exception as e:
                        st.error(f"Feil: {e}")

    return False


def main():
    supabase = get_supabase()

    # ── Session state ──────────────────────────────────────────
    for key, default in {
        "valgt_kode": "",
        "enheter": None,
        "totalt": 0,
        "sok_naeringskode": "",
        "sok_kommune": "",
    }.items():
        if key not in st.session_state:
            st.session_state[key] = default

    # ── Sidebar ───────────────────────────────────────────────
    with st.sidebar:
        st.logo("static/uldre.png", link="https://uldre.no")
        st.markdown("### Næringskode katalog")
        st.markdown("Trykk **Bruk** for å fylle inn koden automatisk.")
        st.markdown("---")

        for bransje, koder in NAERINGSKODER.items():
            with st.expander(bransje):
                for kode, beskrivelse in koder.items():
                    col_kode, col_bruk = st.columns([2, 1])
                    with col_kode:
                        st.markdown(f"`{kode}` {beskrivelse}")
                    with col_bruk:
                        if st.button("Bruk", key=f"btn_{kode}"):
                            st.session_state["valgt_kode"] = kode
                            st.rerun()

        st.markdown("---")
        st.markdown(
            "Dette er bare et lite utvalg av koder. Søk etter andre koder "
            "på [brreg.no](https://www.ssb.no/klass/klassifikasjoner/6)."
        )
        st.markdown("Appen er utviklet av **Uldre**")
        st.markdown("---")
        if st.button("Logg ut", width='stretch'):
            supabase.auth.sign_out()
            st.session_state["session"] = None
            st.rerun()

    # ── Hovedinnhold ──────────────────────────────────────────
    col_left, col_right = st.columns([3, 1])
    with col_left:
        st.title("Bedriftssøk")
        st.markdown("Søk etter bedrifter i Enhetsregisteret basert på næringskode og kommune.")
    with col_right:
        logo = Path(__file__).parent / "static" / "uldre.png"
        if logo.exists():
            st.image(str(logo), width='stretch')

    # ── Inputfelter ───────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        naeringskode = st.text_input(
            "Næringskode",
            value=st.session_state["valgt_kode"],
            help="Velg fra sidebaren eller skriv inn manuelt. F.eks. 56.101 = Restaurant",
        )
    with col2:
        kommune = st.text_input("Kommune", value="")

    sok_knapp = st.button("Søk", type="primary")

    # ── Søkeprosess ───────────────────────────────────────────
    if sok_knapp:
        if not naeringskode or not kommune:
            st.warning("Fyll inn både næringskode og kommune.")
        else:
            with st.spinner(f"Slår opp kommunenummer for {kommune}..."):
                kommunenr = hent_kommunenummer(kommune)

            if not kommunenr:
                st.error(f"Fant ikke kommunenummer for «{kommune}». Sjekk stavemåten.")
            else:
                with st.spinner("Henter bedrifter..."):
                    enheter, totalt = sok_alle_sider(naeringskode, kommunenr)

                if totalt == 0:
                    st.warning(f"Fant ingen bedrifter med næringskode {naeringskode} i {kommune}.")
                else:
                    st.session_state["enheter"]         = enheter
                    st.session_state["totalt"]           = totalt
                    st.session_state["sok_naeringskode"] = naeringskode
                    st.session_state["sok_kommune"]      = kommune

    # ── Vis resultater ────────────────────────────────────────
    if st.session_state["enheter"]:
        sok_kode     = st.session_state["sok_naeringskode"]
        sok_kommunen = st.session_state["sok_kommune"]

        df = bygg_dataframe(st.session_state["enheter"], st.session_state["sok_naeringskode"])

        st.success(
            f"Fant **{st.session_state['totalt']} bedrifter** "
            f"med næringskode {sok_kode} i {sok_kommunen}."
        )

        kun_med_kontakt = st.checkbox("Kun vis bedrifter med telefon eller e-post")
        if kun_med_kontakt:
            df = df[(df["Telefon"] != "–") | (df["E-post"] != "–")]
            st.info(f"Viser {len(df)} bedrifter")
        st.dataframe(df, width='stretch')

        excel_buffer = io.BytesIO()
        df.to_excel(excel_buffer, index=False, engine="openpyxl")
        excel_buffer.seek(0)

        st.download_button(
            label="Last ned som Excel",
            data=excel_buffer,
            file_name=f"bedrifter_{sok_kode.replace('.','_')}_{sok_kommunen}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width='content',
        )


if __name__ == "__main__":
    if init_auth():
        main()
