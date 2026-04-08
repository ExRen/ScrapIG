# import_session.py
# Jalankan script ini SEKALI untuk membuat session file dari Firefox
# Pastikan kamu sudah login Instagram di Firefox sebelum menjalankan ini

from glob import glob
from os.path import expanduser
from platform import system
from sqlite3 import OperationalError, connect

try:
    from instaloader import ConnectionException, Instaloader
except ModuleNotFoundError:
    raise SystemExit("Instaloader not found. Jalankan: pip install instaloader")


def get_firefox_cookiefile():
    """Cari file cookies.sqlite dari Firefox secara otomatis."""
    default_paths = {
        "Windows": "~/AppData/Roaming/Mozilla/Firefox/Profiles/*/cookies.sqlite",
        "Darwin":  "~/Library/Application Support/Firefox/Profiles/*/cookies.sqlite",
    }
    # Default path untuk Linux
    default = default_paths.get(system(), "~/.mozilla/firefox/*/cookies.sqlite")
    files = glob(expanduser(default))

    if not files:
        raise SystemExit(
            "File cookies Firefox tidak ditemukan. "
            "Pastikan Firefox terinstall dan kamu sudah login Instagram di Firefox."
        )
    return files[0]


def import_firefox_session(username: str):
    """
    Import session dari Firefox ke Instaloader.
    'username' adalah username Instagram yang sedang login di Firefox.
    """
    cookiefile = get_firefox_cookiefile()
    print(f"Menggunakan cookies dari: {cookiefile}")

    conn = connect(f"file:{cookiefile}?immutable=1", uri=True)

    try:
        # Coba query dengan baseDomain dulu (format lebih baru)
        cookie_data = conn.execute(
            "SELECT name, value FROM moz_cookies WHERE baseDomain='instagram.com'"
        )
    except OperationalError:
        # Fallback ke format lama
        cookie_data = conn.execute(
            "SELECT name, value FROM moz_cookies WHERE host LIKE '%instagram.com'"
        )

    L = Instaloader(max_connection_attempts=1)
    L.context._session.cookies.update(cookie_data)

    # Verifikasi session berhasil diimpor
    logged_in_as = L.test_login()
    if not logged_in_as:
        raise SystemExit(
            "Session tidak valid. Pastikan kamu login Instagram di Firefox, "
            "lalu coba lagi."
        )

    print(f"✅ Berhasil login sebagai: {logged_in_as}")

    # Simpan session file agar bisa dipakai ulang tanpa buka Firefox lagi
    L.save_session_to_file()
    print(f"Session disimpan ke file. Instaloader bisa dipakai tanpa Firefox sekarang.")

    return L


if __name__ == "__main__":
    ig_username = input("Masukkan username Instagram kamu (yang login di Firefox): ")
    import_firefox_session(ig_username)