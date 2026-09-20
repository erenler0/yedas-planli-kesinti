# YEDAŞ × GSM Kesinti İzleme

Streamlit uygulaması: YEDAŞ planlı kesinti poligonlarını GSM sahalarının enlem/boylam noktalarıyla Shapely Point-in-Polygon ile çakıştırır, haritada gösterir, OSRM ile gerçek sürüş mesafesi hesaplar, arıza/akü sürelerini mum grafiğinde sunar.

Sahte saha verisi yoktur. SQLite şeması boş açılır; sahalar Admin Excel yüklemesiyle gelir.

## Çalıştırma

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS
pip install -r requirements.txt
streamlit run app.py
```

Ana dosya: `app.py` (Streamlit Community Cloud için aynı).

## Streamlit Community Cloud

1. Bu repoyu GitHub’a itin.
2. [share.streamlit.io](https://share.streamlit.io) üzerinde uygulamayı bağlayın.
3. Main file path: `app.py`
4. İsteğe bağlı Secrets:

```
ADMIN_PASSWORD = "admin5555"
```

Cloud diski geçicidir; yeniden başlatmada SQLite sıfırlanabilir. Kalıcı üretim için harici disk veya düzenli Excel senkronu kullanın.

## Eşleştirme

1. Birincil: YEDAŞ `coords` / `geoJson` poligonu ile saha `Latitude`/`Longitude` — Shapely `intersects` / `covers` (içeride veya sınırda).
2. Yedek: YEDAŞ `address` (il / ilçe / mahalle) ile Nominatim’den gelen saha adresi.

YEDAŞ API `st.cache_data(ttl=300)` ile 5 dakikada bir yenilenir.

## Admin

- Şifre (varsayılan): `admin5555`
- Excel sütunları: `KML Dosyası`, `Placemark Adı`, `Açıklama`, `Latitude`, `Longitude`, `Altitude`, `Koordinat (Ham)`
- Yeni sahalar Geopy Nominatim reverse geocoding ile İl / İlçe / Mahalle alır (`User-Agent` + 0.8 sn bekleme).
- İlçe merkezleri boş başlar; “6 il resmi ilçe merkezlerini yükle” veya elle ekleme.
- Saha varsayılan olarak coğrafi en yakın tek ilçe merkezine bağlanır; Admin override edebilir.
- OSRM sonuçları SQLite’da önbelleklenir.

## Dış servisler

| Servis | Kullanım |
| --- | --- |
| `https://www.yedas.com/api/planli-kesinti-harita` | Planlı kesinti |
| Nominatim (Geopy) | Reverse geocoding |
| `https://router.project-osrm.org` | Sürüş km / süre |

Nominatim kullanım politikasına uyun (özel User-Agent, rate limit). Yoğun OSRM için kendi OSRM örneğiniz önerilir.
