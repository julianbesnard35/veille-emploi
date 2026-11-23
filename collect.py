#!/usr/bin/env python3
"""Collecte quotidienne d'offres d'emploi (lancee par GitHub Actions).

Usage : python collect.py <profil> [--vie]
  profil = profil_a | profil_b (cf. config.json)
  --vie  = collecte aussi toutes les offres VIE/VIA recentes de Business France

Ecrit out/<profil>.json (+ out/vie_via.json) : liste d'offres + statut par source.
Ne fait aucun tri fin : la notation et la selection sont faites ensuite par Claude.
"""
import json, re, sys, time, random
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus
from pathlib import Path

import requests
from bs4 import BeautifulSoup

MAX_AGE_DAYS = 6           # marge : Claude applique ensuite la regle des 5 jours
DESC_MAX = 3000            # longueur max des descriptions stockees
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
SKIP_TITLE = re.compile(r"\b(senior|sr\.?|lead|head of|director|directeur|directrice|principal|"
                        r"stage|stagiaire|intern|internship|alternance|alternant|apprenti)\b", re.I)

S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"})
NOW = datetime.now(timezone.utc)
STATUS = {}


def stat(src, **kw):
    s = STATUS.setdefault(src, {"requetes": 0, "erreurs": 0, "offres": 0, "bloque": False, "notes": []})
    for k, v in kw.items():
        if k == "note":
            if len(s["notes"]) < 8:
                s["notes"].append(v)
        elif isinstance(v, bool):
            s[k] = v
        else:
            s[k] += v


def clean(html, n=DESC_MAX):
    if not html:
        return ""
    txt = BeautifulSoup(html, "lxml").get_text(" ", strip=True) if "<" in html else html
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:n]


def get(src, url, **kw):
    stat(src, requetes=1)
    try:
        r = S.get(url, timeout=30, **kw)
        if r.status_code != 200:
            stat(src, erreurs=1, note=f"HTTP {r.status_code} {url[:120]}")
        return r
    except Exception as e:  # noqa
        stat(src, erreurs=1, note=f"{type(e).__name__} {url[:120]}")
        return None


# ---------------------------------------------------------------- LinkedIn
def linkedin(queries):
    src = "linkedin"
    out, seen, blocked = [], set(), False
    base = "https://www.linkedin.com/jobs-guest/jobs/api"

    start = time.time()
    budget = 12 * 60          # 12 min max pour LinkedIn (quota GitHub Actions gratuit)
    consecutive = 0

    def li_get(url):
        """Requete espacee ; sur 429 on patiente puis on reprend (arret apres 4 refus d'affilee)."""
        nonlocal blocked, consecutive
        if time.time() - start > budget:
            blocked = True
            stat(src, bloque=True, note="budget temps LinkedIn epuise")
            return None
        time.sleep(5 + random.random() * 3)
        r = get(src, url)
        if r is not None and r.status_code == 429:
            consecutive += 1
            if consecutive >= 4:
                blocked = True
                stat(src, bloque=True, note="429 repetes : limite LinkedIn, arret")
                return None
            time.sleep(60 * consecutive)
            r = get(src, url)
            if r is not None and r.status_code == 429:
                return r
        consecutive = 0
        return r

    for loc, kw in queries:
        if blocked:
            break
        url = f"{base}/seeMoreJobPostings/search?keywords={quote_plus(kw)}&location={quote_plus(loc)}&f_TPR=r518400&start=0"
        r = li_get(url)
        if r is None or r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for li in soup.select("li"):
            card = li.select_one("[data-entity-urn]")
            urn = card.get("data-entity-urn", "") if card else ""
            m = re.search(r"(\d+)$", urn)
            if not m or m.group(1) in seen:
                continue
            jid = m.group(1)
            seen.add(jid)
            t = li.select_one(".base-search-card__title")
            c = li.select_one(".base-search-card__subtitle")
            l = li.select_one(".job-search-card__location")
            d = li.select_one("time")
            out.append({
                "source": "LinkedIn", "id": jid,
                "titre": t.get_text(strip=True) if t else "",
                "entreprise": c.get_text(strip=True) if c else "",
                "lieu": l.get_text(strip=True) if l else "",
                "date_publication": d.get("datetime", "") if d else "",
                "lien": f"https://www.linkedin.com/jobs/view/{jid}",
                "requete": f"{kw} | {loc}",
            })
    # details (description, niveau, contrat) pour les offres plausibles
    n_detail = 0
    for o in out:
        if blocked or n_detail >= 110:
            o["detail"] = "non recupere (limite LinkedIn)"
            continue
        if SKIP_TITLE.search(o["titre"]):
            o["detail"] = "non recupere (intitule senior/stage/alternance)"
            continue
        n_detail += 1
        r = li_get(f"{base}/jobPosting/{o['id']}")
        if r is None or r.status_code != 200:
            o["detail"] = "non recupere"
            continue
        soup = BeautifulSoup(r.text, "lxml")
        desc = soup.select_one(".show-more-less-html__markup")
        o["description"] = clean(str(desc)) if desc else ""
        crit = {}
        for it in soup.select(".description__job-criteria-item"):
            h = it.select_one("h3"); v = it.select_one("span")
            if h and v:
                crit[h.get_text(strip=True)] = v.get_text(strip=True)
        o["criteres"] = crit
        posted = soup.select_one(".posted-time-ago__text")
        if posted:
            o["publie_il_y_a"] = posted.get_text(strip=True)
    stat(src, offres=len(out))
    return out


# ---------------------------------------------------------------- HelloWork
def hellowork(keywords, location="France"):
    src = "hellowork"
    ids = {}
    for kw in keywords:
        for p in (1, 2):
            url = (f"https://www.hellowork.com/fr-fr/emploi/recherche.html?k={quote_plus(kw)}"
                   f"&l={quote_plus(location)}&d=w&p={p}")
            r = get(src, url)
            time.sleep(0.6)
            if r is None or r.status_code != 200:
                break
            found = re.findall(r"/fr-fr/emplois/(\d+)\.html", r.text)
            for i in found:
                ids.setdefault(i, kw)
            if len(set(found)) < 15:
                break
    out = []
    for jid, kw in list(ids.items())[:350]:
        r = get(src, f"https://www.hellowork.com/fr-fr/emplois/{jid}.html")
        time.sleep(0.4)
        if r is None or r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        jp = None
        for sc in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(sc.string or "")
            except Exception:
                continue
            for d in (data if isinstance(data, list) else [data]):
                if isinstance(d, dict) and d.get("@type") == "JobPosting":
                    jp = d
        if not jp:
            continue
        dp = jp.get("datePosted", "")
        if not fresh(dp):
            continue
        loc = jp.get("jobLocation") or {}
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
        badges = [b.get_text(" ", strip=True) for b in soup.select("li, span")
                  if re.match(r"^\s*Exp\.", b.get_text(" ", strip=True) or "")][:2]
        out.append({
            "source": "HelloWork", "id": jid,
            "titre": jp.get("title", ""),
            "entreprise": (jp.get("hiringOrganization") or {}).get("name", ""),
            "lieu": " ".join(x for x in [addr.get("addressLocality", ""), addr.get("postalCode", "")] if x),
            "date_publication": dp,
            "contrat": jp.get("employmentType", ""),
            "salaire": json.dumps(jp.get("baseSalary"), ensure_ascii=False) if jp.get("baseSalary") else "",
            "experience": " / ".join(dict.fromkeys(badges)),
            "description": clean(jp.get("description", "")),
            "lien": f"https://www.hellowork.com/fr-fr/emplois/{jid}.html",
            "requete": kw,
        })
    stat(src, offres=len(out))
    return out


# ---------------------------------------------------------------- Guichet-Emplois (Canada)
def guichetemplois(queries):
    src = "guichetemplois"
    out, seen = [], set()
    for kw, loc in queries:
        url = (f"https://www.guichetemplois.gc.ca/jobsearch/rechercheemplois?searchstring={quote_plus(kw)}"
               f"&locationstring={quote_plus(loc)}&sort=D&fage=7")
        r = get(src, url)
        time.sleep(0.8)
        if r is None or r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for art in soup.select("article"):
            a = art.select_one("a[href]")
            if not a:
                continue
            href = a["href"].split(";")[0].split("?")[0]
            if href in seen:
                continue
            seen.add(href)
            g = lambda sel: (art.select_one(sel).get_text(" ", strip=True) if art.select_one(sel) else "")
            out.append({
                "source": "Guichet-Emplois", "id": href.rsplit("/", 1)[-1],
                "titre": g(".noctitle"), "entreprise": g(".business"),
                "lieu": g(".location").replace("Lieu", "").strip(), "date_publication": g(".date"),
                "salaire": g(".salary"),
                "lien": "https://www.guichetemplois.gc.ca" + href if href.startswith("/") else href,
                "requete": f"{kw} | {loc}",
            })
    for o in out[:80]:
        r = get(src, o["lien"])
        time.sleep(0.5)
        if r is not None and r.status_code == 200:
            soup = BeautifulSoup(r.text, "lxml")
            main = soup.select_one("main") or soup
            o["description"] = clean(str(main), 2500)
    stat(src, offres=len(out))
    return out


# ---------------------------------------------------------------- navigateur sans tete (Playwright)
NUXT_JS = """async (skip) => {
  function find(c){ if(c && c.$data && Array.isArray(c.$data.offres)) return c;
    for (const ch of (c && c.$children) || []) { const r = find(ch); if (r) return r; } return null; }
  const comp = find(window.$nuxt);
  if (!comp) return {error: 'composant introuvable'};
  const t = comp.searchOffers.bySearchQuery;
  const req = comp.getRequest({limit: 200, skip: skip, latest: ["true"]}, t.expectedData, null);
  const r = await fetch(t.url, req);
  if (!r.ok) return {error: 'HTTP ' + r.status};
  return await r.json();
}"""


def vie_via():
    src = "vie_via"
    from playwright.sync_api import sync_playwright
    out = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(user_agent=UA, locale="fr-FR")
        stat(src, requetes=1)
        pg.goto("https://mon-vie-via.businessfrance.fr/offres/recherche?latest=true", timeout=60000)
        pg.wait_for_function("window.$nuxt", timeout=60000)
        pg.wait_for_timeout(4000)
        skip = 0
        while skip < 3000:
            stat(src, requetes=1)
            j = pg.evaluate(NUXT_JS, skip)
            if not isinstance(j, dict) or j.get("error"):
                stat(src, erreurs=1, note=str(j.get("error") if isinstance(j, dict) else j)[:150])
                break
            res = j.get("result", [])
            stop = False
            for x in res:
                dp = x.get("startBroadcastDate", "")
                if not fresh(dp):
                    stop = True
                    continue
                out.append({
                    "source": "Business France", "id": x.get("id"),
                    "titre": x.get("missionTitle", ""), "entreprise": x.get("organizationName", ""),
                    "pays": x.get("countryName", ""), "ville": x.get("cityName", ""),
                    "type": x.get("missionType", ""), "duree_mois": x.get("missionDuration", ""),
                    "debut": x.get("missionStartDate", ""), "indemnite": x.get("indemnite", ""),
                    "date_publication": dp, "date_expiration": x.get("endBroadcastDate", ""),
                    "presentation_entreprise": clean(x.get("organizationPresentation", ""), 600),
                    "description": clean(x.get("missionDescription", ""), 2200),
                    "profil_ideal": clean(x.get("missionProfile", ""), 1500),
                    "lien": f"https://mon-vie-via.businessfrance.fr/offres/{x.get('id')}",
                })
            if stop or len(res) < 200:
                break
            skip += 200
        b.close()
    if not out:
        stat(src, bloque=True)
    stat(src, offres=len(out))
    return out


APEC_SEARCH_JS = """async (kw) => {
  const body = {lieux:[],fonctions:[],statutPoste:[],typesContrat:[],typesConvention:[],niveauxExperience:[],
    idsEtablissement:[],secteursActivite:[],typesTeletravail:[],idNomZonesDeplacement:[],positionNumbersExcluded:[],
    typeClient:"CADRE",sorts:[{type:"DATE",direction:"DESCENDING"}],pagination:{range:100,startIndex:0},
    activeFiltre:true,pointGeolocDeReference:{distance:0},motsCles:kw,anciennetePublication:"101851"};
  const r = await fetch('/cms/webservices/rechercheOffre', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  if (!r.ok) return {error: 'HTTP ' + r.status};
  return await r.json();
}"""
APEC_DETAIL_JS = """async (nums) => {
  const out = {};
  for (let i = 0; i < nums.length; i += 4) {
    await Promise.all(nums.slice(i, i + 4).map(async n => {
      try { const r = await fetch('/cms/webservices/offre/public?numeroOffre=' + n); out[n] = r.ok ? await r.json() : {error: r.status}; }
      catch (e) { out[n] = {error: String(e)}; }
    }));
  }
  return out;
}"""


def apec(keywords):
    src = "apec"
    from playwright.sync_api import sync_playwright
    nums, out = {}, []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(user_agent=UA, locale="fr-FR")
        stat(src, requetes=1)
        pg.goto("https://www.apec.fr/candidat/recherche-emploi.html/emploi", timeout=60000)
        pg.wait_for_timeout(4000)
        for kw in keywords:
            stat(src, requetes=1)
            j = pg.evaluate(APEC_SEARCH_JS, kw)
            if not isinstance(j, dict) or j.get("error"):
                stat(src, erreurs=1, note=f"recherche {kw}: {j.get('error') if isinstance(j, dict) else j}")
                continue
            for x in j.get("resultats", []):
                n = x.get("numeroOffre")
                if n:
                    nums.setdefault(n, kw)
        todo = list(nums.items())[:300]
        details = {}
        for i in range(0, len(todo), 40):
            chunk = [n for n, _ in todo[i:i + 40]]
            stat(src, requetes=len(chunk))
            details.update(pg.evaluate(APEC_DETAIL_JS, chunk) or {})
        b.close()
    for n, kw in todo if nums else []:
        d = details.get(n) or {}
        if d.get("error") is not None and len(d) == 1:
            stat(src, erreurs=1)
            continue
        dp = d.get("datePremierePublication") or d.get("datePublication") or ""
        if not fresh(dp):
            continue
        lieux = d.get("lieux") or []
        lieu = ", ".join(l.get("libelleLieu", "") for l in lieux if isinstance(l, dict)) or d.get("lieuTexte", "")
        out.append({
            "source": "APEC", "id": n,
            "titre": d.get("intitule", ""),
            "entreprise": d.get("nomCompteEtablissement") or d.get("enseigne") or d.get("nomCommercial") or "",
            "lieu": lieu,
            "date_publication": dp,
            "contrat": str(d.get("typeContrat", "") or d.get("libelleTypeContrat", "")),
            "salaire": d.get("salaireTexte", "") or "",
            "experience": str(d.get("experience", "") or d.get("libelleExperience", "") or ""),
            "description": clean(d.get("texteHtml", ""), 1800),
            "profil": clean(d.get("texteHtmlProfil", ""), 1500),
            "lien": f"https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/{n}",
            "requete": kw,
        })
    if not out and not nums:
        stat(src, bloque=True)
    stat(src, offres=len(out))
    return out


# ---------------------------------------------------------------- utilitaires
def fresh(s):
    """True si la date (ISO) est dans les MAX_AGE_DAYS derniers jours ; True si illisible (Claude tranchera)."""
    if not s:
        return True
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00")[:25])
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d >= NOW - timedelta(days=MAX_AGE_DAYS)
    except Exception:
        return True


def run_step(name, fn, *a):
    t = time.time()
    try:
        res = fn(*a)
    except Exception as e:  # une source en panne ne doit pas bloquer les autres
        stat(name, erreurs=1, bloque=True, note=f"plantage : {type(e).__name__}: {e}"[:200])
        res = []
    stat(name, note=f"duree {int(time.time() - t)} s")
    print(f"[{name}] {len(res)} offres, statut={STATUS.get(name)}", flush=True)
    return res


def main():
    profile = sys.argv[1]
    cfg = json.loads(Path(__file__).with_name("config.json").read_text())[profile]
    outdir = Path("out"); outdir.mkdir(exist_ok=True)
    import os
    only = [x for x in os.environ.get("SOURCES", "all").split(",") if x]
    want = lambda n: "all" in only or n in only
    offers = []
    if want("hellowork"):
        offers += run_step("hellowork", hellowork, cfg.get("hellowork", []))
    if want("apec"):
        offers += run_step("apec", apec, cfg.get("apec", []))
    if want("guichetemplois"):
        offers += run_step("guichetemplois", guichetemplois, cfg.get("guichetemplois", []))
    if "--vie" in sys.argv and want("vie_via"):
        vie = run_step("vie_via", vie_via)
        (outdir / "vie_via.json").write_text(json.dumps(
            {"genere_le": NOW.isoformat(), "statut": STATUS.get("vie_via"), "offres": vie},
            ensure_ascii=False, indent=1))
    if want("linkedin"):
        offers += run_step("linkedin", linkedin, cfg.get("linkedin", []))
    (outdir / f"{profile}.json").write_text(json.dumps(
        {"genere_le": NOW.isoformat(), "profil": profile, "statut_par_source": STATUS, "offres": offers},
        ensure_ascii=False, indent=1))
    print(json.dumps(STATUS, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
