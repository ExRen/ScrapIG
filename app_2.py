"""
Instagram Scraper — Powered by instagrapi (Instagram Private Mobile API)
=========================================================================
Instalasi : pip install -r requirements.txt
Jalankan  : streamlit run app.py
"""

import os
import time
import requests
import streamlit as st
from instagrapi import Client
from instagrapi.exceptions import (
    LoginRequired, ChallengeRequired,
    BadPassword, UserNotFound, TwoFactorRequired,
)
import pandas as pd
from datetime import date, datetime, timezone


# ══════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG & STYLE
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Instagram Scraper",
    page_icon="📸",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main-title {
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(135deg, #833ab4 0%, #fd1d1d 50%, #fcb045 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        line-height: 1.2;
    }
    div[data-testid="metric-container"] {
        background: rgba(131, 58, 180, 0.07);
        border: 1px solid rgba(131, 58, 180, 0.2);
        border-radius: 10px;
        padding: 12px 16px;
    }
</style>
""", unsafe_allow_html=True)

SESSION_FILE = "ig_session.json"


# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════

st.markdown('<p class="main-title">📸 Instagram Scraper</p>', unsafe_allow_html=True)
st.caption(
    "Powered by **instagrapi** (Instagram Private Mobile API) — "
    "caption penuh, download foto & video, tahan rate-limit."
)
st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def build_client_from_session(session_file: str) -> Client | None:
    """
    Coba load session dari file JSON yang sudah tersimpan.
    Session berisi cookies + device fingerprint sehingga tidak perlu login ulang.
    Return None jika file tidak ada atau session sudah expired.
    """
    if not os.path.exists(session_file):
        return None
    try:
        cl = Client()
        cl.load_settings(session_file)
        cl.get_timeline_feed()   # request ringan untuk verifikasi
        return cl
    except Exception:
        try:
            os.remove(session_file)
        except OSError:
            pass
        return None


def build_client_from_credentials(
    username: str,
    password: str,
    totp_code: str = "",
    session_file: str = SESSION_FILE,
) -> Client:
    """
    Login dengan username + password. instagrapi mensimulasikan device Android
    lengkap — request terlihat seperti dari aplikasi Instagram di HP.
    Session disimpan ke file JSON agar run berikutnya tidak perlu login ulang.
    """
    cl = Client()
    cl.set_locale("id_ID")
    cl.set_timezone_offset(7 * 3600)   # WIB = UTC+7

    if totp_code:
        cl.login(username, password, verification_code=totp_code)
    else:
        cl.login(username, password)

    cl.dump_settings(session_file)
    return cl


# ══════════════════════════════════════════════════════════════════════════════
# SCRAPING HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def get_profile_info(cl: Client, username: str) -> dict:
    """
    Ambil info profil via Private Mobile API saja.
    user_info_by_username_v1() memanggil /api/v1/users/{username}/usernameinfo/
    — tidak menyentuh endpoint publik web_profile_info yang selalu kena 429.
    """
    info = cl.user_info_by_username_v1(username)
    return {
        "_user_id"        : info.pk,
        "Nama Lengkap"    : info.full_name or "-",
        "Username"        : info.username,
        "Bio"             : info.biography or "-",
        "Followers"       : info.follower_count,
        "Following"       : info.following_count,
        "Total Postingan" : info.media_count,
        "Akun Bisnis"     : "Ya" if info.is_business else "Tidak",
        "Terverifikasi"   : "✅" if info.is_verified else "Tidak",
        "Kategori"        : info.category or "-",
        "External URL"    : str(info.external_url) if info.external_url else "-",
        "Link Profil"     : f"https://www.instagram.com/{info.username}/",
    }


def scrape_posts(
    cl: Client,
    user_id: int,
    start: date,
    end: date,
    max_count: int,
    progress_callback=None,
    fetch_comments: bool = False,
    max_comments: int = 10,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Ambil postingan via user_medias_v1() — Private Mobile API.

    Perubahan penting vs versi sebelumnya:
    1. Caption diambil PENUH tanpa pemotongan karakter.
    2. Hashtag & mention TIDAK dipisahkan ke kolom sendiri karena sudah
       merupakan bagian alami dari caption asli.
    3. Gunakan `continue` (bukan `break`) untuk filter tanggal agar satu
       post dengan timezone aneh tidak memotong seluruh iterasi.
    4. _media_obj disimpan di setiap row untuk keperluan download media.
    """
    start_dt = datetime.combine(start, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_dt   = datetime.combine(end,   datetime.max.time()).replace(tzinfo=timezone.utc)

    posts_data = []
    comments_data = []
    count      = 0

    # user_medias_v1() = /api/v1/feed/user/{pk}/ — Private Mobile API eksklusif.
    # Fetch dengan buffer agar filter tanggal punya cukup material.
    medias = cl.user_medias_v1(user_id, amount=min(max_count + 50, 500))

    for media in medias:
        # Normalisasi timezone ke UTC.
        taken_at = media.taken_at
        if taken_at.tzinfo is None:
            taken_at = taken_at.replace(tzinfo=timezone.utc)

        # Filter tanggal: gunakan continue (bukan break) agar setiap post
        # dievaluasi independen. break dulu menjadi penyebab 0 post.
        if taken_at < start_dt or taken_at > end_dt:
            continue

        # Label tipe media yang lebih ramah
        tipe_map   = {1: "Foto", 2: "Video/Reel", 8: "Carousel/Album"}
        media_type = tipe_map.get(media.media_type, f"Tipe-{media.media_type}")

        # Caption PENUH — tidak ada pemotongan.
        # Hashtag dan mention sudah ada di dalam caption, jadi tidak perlu kolom
        # terpisah. Memisahkannya justru membuat caption terasa tidak lengkap.
        caption_text = media.caption_text or ""

        # URL media representatif (untuk preview & download)
        if media.media_type == 2:
            # Video/Reel: ambil video_url (.mp4), fallback ke thumbnail
            media_url = str(media.video_url) if media.video_url else str(media.thumbnail_url or "")
        elif media.media_type == 8:
            # Carousel: gunakan resource pertama sebagai representasi
            if media.resources:
                r0 = media.resources[0]
                media_url = str(r0.video_url if r0.video_url else r0.thumbnail_url or "")
            else:
                media_url = ""
        else:
            # Foto biasa
            media_url = str(media.thumbnail_url or "")

        posts_data.append({
            "No"         : count + 1,
            "Tanggal"    : taken_at.astimezone().strftime("%Y-%m-%d %H:%M"),
            "Tipe"       : media_type,
            "Caption"    : caption_text,       # Full caption, tidak dipotong
            "Likes"      : media.like_count or 0,
            "Komentar"   : media.comment_count or 0,
            "Lokasi"     : media.location.name if media.location else "-",
            "URL Post"   : f"https://www.instagram.com/p/{media.code}/",
            "Shortcode"  : media.code,
            "Media URL"  : media_url,
            "_media_obj" : media,              # Object asli — untuk download carousel
        })

        # Ambil komentar jika diminta
        if fetch_comments and (media.comment_count and media.comment_count > 0):
            try:
                time.sleep(1) # Delay untuk mencegah blokir/rate-limit API
                comments = cl.media_comments(media.id, amount=max_comments)
                for c in comments:
                    comments_data.append({
                        "Shortcode" : media.code,
                        "Username"  : c.user.username if getattr(c, "user", None) else "-",
                        "Komentar"  : c.text,
                        "Tanggal"   : c.created_at_utc.astimezone().strftime("%Y-%m-%d %H:%M:%S") if getattr(c, "created_at_utc", None) else "-",
                        "Likes"     : getattr(c, "like_count", 0)
                    })
            except Exception:
                pass

        count += 1
        if progress_callback:
            progress_callback(count, max_count)
        if count >= max_count:
            break

        time.sleep(0.3)

    return pd.DataFrame(posts_data), pd.DataFrame(comments_data)


# ══════════════════════════════════════════════════════════════════════════════
# DOWNLOAD HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _download_url(url: str, dest_path: str) -> None:
    """
    Unduh satu file dari URL dan simpan ke dest_path.
    Idempotent: skip jika file sudah ada, tidak perlu download ulang.
    """
    if os.path.exists(dest_path):
        return
    resp = requests.get(url, timeout=30, stream=True)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)


def download_media_files(
    df: pd.DataFrame,
    target_username: str,
    status_placeholder,
) -> tuple[str, int, int]:
    """
    Download semua foto dan video dari DataFrame hasil scraping.

    Konvensi naming file: {tanggal}_{shortcode}[_slideNN].{ext}
    Ini memudahkan pencarian manual dan pengurutan kronologis di file explorer.

    Strategi per tipe:
    - Foto (1)           : satu file .jpg
    - Video/Reel (2)     : satu file .mp4 (atau .jpg jika video_url tidak ada)
    - Carousel/Album (8) : satu file per slide, suffix _slide01, _slide02, dst.
    """
    folder = os.path.join("downloads", target_username)
    os.makedirs(folder, exist_ok=True)

    total     = len(df)
    succeeded = 0
    failed    = 0

    for i, row in df.iterrows():
        media     = row["_media_obj"]
        shortcode = row["Shortcode"]
        tanggal   = row["Tanggal"].replace(":", "-").replace(" ", "_")
        prefix    = f"{tanggal}_{shortcode}"

        status_placeholder.caption(
            f"⬇️ [{i + 1}/{total}] Mengunduh {shortcode} ({row['Tipe']}) …"
        )

        try:
            if media.media_type == 1:
                # ── Foto biasa ────────────────────────────────────────────
                dest = os.path.join(folder, f"{prefix}.jpg")
                _download_url(str(media.thumbnail_url), dest)

            elif media.media_type == 2:
                # ── Video / Reel ──────────────────────────────────────────
                if media.video_url:
                    dest = os.path.join(folder, f"{prefix}.mp4")
                    _download_url(str(media.video_url), dest)
                else:
                    dest = os.path.join(folder, f"{prefix}.jpg")
                    _download_url(str(media.thumbnail_url), dest)

            elif media.media_type == 8:
                # ── Carousel: download setiap slide ──────────────────────
                for idx_r, resource in enumerate(media.resources, start=1):
                    if resource.video_url:
                        ext = "mp4"
                        url = str(resource.video_url)
                    else:
                        ext = "jpg"
                        url = str(resource.thumbnail_url)
                    dest = os.path.join(folder, f"{prefix}_slide{idx_r:02d}.{ext}")
                    _download_url(url, dest)

            succeeded += 1

        except Exception:
            failed += 1
            continue  # Lanjut ke post berikutnya meski satu gagal

        time.sleep(0.5)  # Jeda antar file agar tidak terlalu agresif

    return folder, succeeded, failed


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.header("⚙️ Konfigurasi Scraper")

    # ── Login ─────────────────────────────────────────────────────────────────
    st.subheader("🔐 Login Instagram")
    st.caption("Gunakan akun **dummy/sekunder**, bukan akun utama.")

    session_exists = os.path.exists(SESSION_FILE)
    if session_exists:
        st.success("✅ Session tersimpan — login otomatis aktif.", icon="💾")
        st.caption("Hapus `ig_session.json` jika ingin ganti akun.")
    else:
        st.info("Belum ada session. Isi kredensial untuk login pertama kali.")

    ig_user = st.text_input(
        "Username akun kamu (bukan target)",
        placeholder="username_dummy",
    )
    ig_pass = st.text_input("Password", type="password")

    with st.expander("🔑 Kode 2FA (jika aktif)", expanded=False):
        totp_code = st.text_input(
            "Kode TOTP / One-Time Password",
            placeholder="123456",
            help="Isi jika akun menggunakan Google Authenticator atau sejenisnya.",
        )

    st.divider()

    # ── Target ────────────────────────────────────────────────────────────────
    st.subheader("🎯 Target Akun")
    target_username = st.text_input(
        "Username target",
        placeholder="asabri_official",
        help="Tanpa '@'. Hanya akun publik atau akun yang sudah kamu follow.",
    )

    st.divider()

    # ── Filter Tanggal ────────────────────────────────────────────────────────
    st.subheader("📅 Filter Tanggal")
    col_a, col_b = st.columns(2)
    with col_a:
        start_date = st.date_input(
            "Dari",
            value=date(2024, 1, 1),
            min_value=date(2010, 1, 1),
            max_value=date.today(),
            format="YYYY-MM-DD",
        )
    with col_b:
        end_date = st.date_input(
            "Sampai",
            value=date.today(),
            min_value=date(2010, 1, 1),
            max_value=date.today(),
            format="YYYY-MM-DD",
        )

    st.divider()

    # ── Opsi ──────────────────────────────────────────────────────────────────
    st.subheader("🔧 Opsi")
    max_posts = st.slider(
        "Maks. postingan diambil",
        min_value=5, max_value=200, value=30, step=5,
    )
    
    get_comments = st.checkbox("💬 Ambil Teks Komentar", value=False, help="Akan mengambil daftar teks komentar dari setiap postingan (Proses akan lebih lama).")
    max_comments = 0
    if get_comments:
        max_comments = st.number_input("Maks. komentar per post", min_value=1, max_value=200, value=10, step=5)
        
    save_csv = st.checkbox("💾 Simpan metadata sebagai CSV", value=True)
    download_media = st.checkbox(
        "📥 Download foto & video ke lokal",
        value=False,
        help=(
            "Menyimpan semua file media ke folder downloads/{username}/. "
            "Foto → .jpg | Video/Reel → .mp4 | Carousel → semua slide diunduh."
        ),
    )
    if download_media:
        st.caption(
            "⚠️ Download akan lebih lama. "
            "File disimpan di `downloads/{username}/` relatif terhadap app.py."
        )

    if session_exists:
        st.divider()
        if st.button("🗑️ Hapus Session Tersimpan", use_container_width=True):
            try:
                os.remove(SESSION_FILE)
                st.success("Session dihapus. Silakan login ulang.")
                st.rerun()
            except OSError:
                st.error("Gagal menghapus file session.")

    st.divider()
    run_btn = st.button("🚀 Mulai Scraping", use_container_width=True, type="primary")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN — Eksekusi
# ══════════════════════════════════════════════════════════════════════════════

if run_btn:

    # ── Validasi ──────────────────────────────────────────────────────────────
    if not target_username.strip():
        st.error("❌ Username target belum diisi.")
        st.stop()
    if start_date > end_date:
        st.error("❌ Tanggal mulai tidak boleh lebih besar dari tanggal akhir.")
        st.stop()

    clean_target = target_username.strip().lstrip("@")
    clean_user   = ig_user.strip() if ig_user else ""
    clean_pass   = ig_pass.strip() if ig_pass else ""
    totp         = totp_code.strip() if totp_code else ""

    # ── Login ──────────────────────────────────────────────────────────────────
    cl = None
    with st.spinner("Memeriksa session …"):
        cl = build_client_from_session(SESSION_FILE)

    if cl:
        st.success("✅ Login berhasil via session file tersimpan.")
    else:
        if not clean_user or not clean_pass:
            st.error(
                "❌ Belum ada session dan kredensial belum diisi. "
                "Isi username & password di sidebar."
            )
            st.stop()

        with st.spinner(f"Login sebagai @{clean_user} …"):
            try:
                cl = build_client_from_credentials(clean_user, clean_pass, totp, SESSION_FILE)
                st.success(f"✅ Login berhasil sebagai @{clean_user}. Session disimpan.")
            except BadPassword:
                st.error("❌ Password salah.")
                st.stop()
            except TwoFactorRequired:
                st.error("❌ Akun ini menggunakan 2FA. Isi kode TOTP di sidebar lalu coba lagi.")
                st.stop()
            except ChallengeRequired:
                st.error(
                    "⚠️ Instagram mengirim challenge. Buka aplikasi Instagram di HP, "
                    "selesaikan verifikasi yang muncul, lalu coba lagi."
                )
                st.stop()
            except Exception as e:
                st.error(f"❌ Login gagal: {e}")
                st.stop()

    # ── Profil target ──────────────────────────────────────────────────────────
    with st.spinner(f"Mengambil profil @{clean_target} …"):
        try:
            profile_info = get_profile_info(cl, clean_target)
        except UserNotFound:
            st.error(f"❌ Akun @{clean_target} tidak ditemukan atau sudah tidak aktif.")
            st.stop()
        except LoginRequired:
            st.error("❌ Akun ini privat. Kamu harus follow-nya terlebih dahulu.")
            st.stop()
        except Exception as e:
            st.error(f"❌ Gagal mengambil profil: {e}")
            st.stop()

    user_id = profile_info.pop("_user_id")

    st.subheader(f"👤 @{profile_info['Username']} — {profile_info['Nama Lengkap']}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("👥 Followers",    f"{profile_info['Followers']:,}")
    c2.metric("➡️ Following",    f"{profile_info['Following']:,}")
    c3.metric("📷 Total Post",   f"{profile_info['Total Postingan']:,}")
    c4.metric("✅ Terverifikasi", profile_info["Terverifikasi"])

    with st.expander("ℹ️ Detail Lengkap Profil"):
        for k, v in profile_info.items():
            if k == "Link Profil":
                st.markdown(f"[🔗 Buka di Instagram]({v})")
            else:
                st.write(f"**{k}:** {v}")

    st.divider()

    # ── Scraping postingan ─────────────────────────────────────────────────────
    st.subheader(f"📥 Mengambil Postingan: {start_date} → {end_date}")
    progress_bar = st.progress(0, text="Memulai …")
    status_text  = st.empty()

    def update_progress(current: int, total: int):
        pct = min(int(current / total * 100), 99)
        progress_bar.progress(pct, text=f"Post ke-{current} dari maks. {total} …")
        status_text.caption(f"🔄 Memproses postingan ke-{current} …")

    try:
        df, df_comments = scrape_posts(
            cl=cl,
            user_id=user_id,
            start=start_date,
            end=end_date,
            max_count=max_posts,
            progress_callback=update_progress,
            fetch_comments=get_comments,
            max_comments=max_comments,
        )
    except Exception as e:
        progress_bar.empty()
        status_text.empty()
        st.error(f"❌ Error saat mengambil postingan: {e}")
        st.stop()

    progress_bar.progress(100, text="✅ Metadata berhasil diambil!")
    status_text.empty()

    if df.empty:
        st.warning(
            f"Tidak ada postingan ditemukan untuk **@{clean_target}** "
            f"antara {start_date} dan {end_date}."
        )
        st.stop()

    # ── Download foto & video ──────────────────────────────────────────────────
    if download_media:
        st.divider()
        st.subheader("📥 Download Foto & Video")
        dl_status   = st.empty()
        dl_progress = st.progress(0, text="Mempersiapkan download …")
        try:
            folder, ok, fail = download_media_files(df, clean_target, dl_status)
            dl_progress.progress(100, text="✅ Download selesai!")
            dl_status.empty()
            st.success(
                f"✅ Berhasil download **{ok}** file, gagal **{fail}** file. "
                f"Disimpan di: `{os.path.abspath(folder)}`"
            )
        except Exception as e:
            dl_progress.empty()
            dl_status.empty()
            st.error(f"❌ Error saat download: {e}")

    # ── Statistik ─────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("📊 Statistik Ringkas")
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("📝 Post Ditemukan", f"{len(df):,}")
    s2.metric("👍 Total Likes",    f"{df['Likes'].sum():,}")
    s3.metric("💬 Total Komentar", f"{df['Komentar'].sum():,}")
    s4.metric("❤️ Rata² Likes",    f"{df['Likes'].mean():.1f}")
    s5.metric("💬 Rata² Komentar", f"{df['Komentar'].mean():.1f}")

    # ── Grafik tren ───────────────────────────────────────────────────────────
    st.subheader("📈 Tren Likes & Komentar")
    chart_df = df[["Tanggal", "Likes", "Komentar"]].copy()
    chart_df["Tanggal"] = pd.to_datetime(chart_df["Tanggal"])
    chart_df = chart_df.set_index("Tanggal").sort_index()
    st.line_chart(chart_df, use_container_width=True)

    # ── Distribusi tipe konten ────────────────────────────────────────────────
    st.subheader("🗂️ Distribusi Tipe Konten")
    tipe_df = df["Tipe"].value_counts().reset_index()
    tipe_df.columns = ["Tipe", "Jumlah"]
    st.bar_chart(tipe_df.set_index("Tipe"))

    # ── Tabel data postingan ──────────────────────────────────────────────────
    st.subheader("📋 Data Postingan")
    # Kolom yang ditampilkan — _media_obj tidak ditampilkan (internal object)
    display_cols = ["No", "Tanggal", "Tipe", "Likes", "Komentar",
                    "Caption", "Lokasi", "URL Post"]
    st.dataframe(
        df[display_cols],
        use_container_width=True,
        column_config={
            "URL Post": st.column_config.LinkColumn("URL Post", display_text="🔗 Buka"),
            "Likes"   : st.column_config.NumberColumn(format="%d 👍"),
            "Komentar": st.column_config.NumberColumn(format="%d 💬"),
            "No"      : st.column_config.NumberColumn(width="small"),
            "Caption" : st.column_config.TextColumn(width="large"),
        },
        hide_index=True,
    )

    if get_comments and not df_comments.empty:
        st.subheader("💬 Data Teks Komentar")
        st.dataframe(
            df_comments,
            use_container_width=True,
            column_config={
                "Likes" : st.column_config.NumberColumn(format="%d 👍"),
            },
            hide_index=True,
        )

    # ── Download CSV ──────────────────────────────────────────────────────────
    if save_csv:
        st.divider()
        st.subheader("⬇️ Download Data")
        # Buang kolom internal _media_obj sebelum export ke CSV
        csv_df       = df.drop(columns=["_media_obj"], errors="ignore")
        csv_filename = f"{clean_target}_{start_date}_{end_date}.csv"
        
        btn_c1, btn_c2 = st.columns(2)
        with btn_c1:
            st.download_button(
                label="⬇️ Download Data Postingan (CSV)",
                data=csv_df.to_csv(index=False).encode("utf-8"),
                file_name=csv_filename,
                mime="text/csv",
                use_container_width=True,
            )
        
        if get_comments and not df_comments.empty:
            comments_filename = f"{clean_target}_{start_date}_{end_date}_comments.csv"
            with btn_c2:
                st.download_button(
                    label="⬇️ Download Data Komentar (CSV)",
                    data=df_comments.to_csv(index=False).encode("utf-8"),
                    file_name=comments_filename,
                    mime="text/csv",
                    use_container_width=True,
                )

# ── Halaman awal ──────────────────────────────────────────────────────────────
else:
    col_l, col_r = st.columns([3, 2], gap="large")

    with col_l:
        st.markdown("""
        ### 👋 Cara Penggunaan

        **Login** menggunakan akun Instagram dummy/sekunder milikmu sendiri
        (bukan akun target). Setelah berhasil, session disimpan ke file
        `ig_session.json` sehingga run berikutnya otomatis login tanpa isi
        kredensial lagi.

        **Isi target & filter tanggal**, lalu tentukan jumlah maksimum postingan.
        Semua postingan dalam rentang tanggal yang dipilih akan diambil
        dengan caption **penuh** dan tipe media yang akurat.

        **Opsi Download Foto & Video** — jika diaktifkan, scraper akan
        mengunduh setiap file media ke folder `downloads/{username}/` secara
        otomatis. Foto disimpan sebagai `.jpg`, video dan reel sebagai `.mp4`,
        dan setiap slide dari carousel diunduh secara individual.
        """)

    with col_r:
        st.markdown("""
        ### 📁 Format File Download

        File disimpan dengan naming convention:
        `{tanggal}_{shortcode}.{ext}`

        Untuk carousel, setiap slide diberi suffix:
        `{tanggal}_{shortcode}_slide01.jpg`, `_slide02.jpg`, dst.

        Format ini memudahkan pengurutan kronologis langsung
        di file explorer tanpa perlu rename manual.
        """)

        st.info(
            "🔒 Hanya akun **publik** yang bisa di-scrape tanpa follow. "
            "Gunakan secara bertanggung jawab untuk keperluan "
            "monitoring media internal.",
            icon="ℹ️",
        )