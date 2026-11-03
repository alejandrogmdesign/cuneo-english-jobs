"""Decide whether a school notice is English-teaching contract work, and pull out dates.

Plain regular expressions only (no AI). Tuned on Italian school notices: calls for an
"esperto (madrelingua) inglese", lettorato, Cambridge/Trinity exam courses, and so on.
Interpelli and supplenze are out of scope (they need an Italian teaching qualification).
"""
import re
import datetime as dt

ENGLISH = re.compile(
    r"ingles|english|\bA\s?B\s?-?\s?2[45]\b|\bB\s?B\s?-?\s?0?2\b|native\s+speaker|"
    r"cambridge|trinity|\bielts\b|\bket\b|\bpet\b|\bfce\b|\bcae\b|first\s+certificate",
    re.I,
)
OTHER_LANGUAGE = re.compile(r"frances|tedesc|spagnol|cines|russ[oa]\b|arab[oa]\b|\bDELF\b|\bDELE\b|goethe", re.I)
CONTRACT = re.compile(
    r"madre\s?-?lingua|native\s+speaker|espert[oiae]\b|conversa(zione|tore|trice)|lettor(at)?[oei]|"
    r"collaborazion\w+\s+(plurim|occasional)|incaric\w+|prestazion\w+\s+(d'?opera|occasional|professional)|"
    r"avviso\s+(pubblico\s+)?(di\s+|per\s+(la\s+)?)?(selezione|reclutamento|individuazione|manifestazione)|"
    r"manifestazion\w+\s+d'?interesse|bando|selezione|reclutamento|formator[ei]|docenz[ae]\s+estern|"
    r"\bclil\b|certificazion\w+|corso\s+di\s+(lingua\s+)?ingles|cercasi|teacher",
    re.I,
)
# Interpelli, supplenze and messa a disposizione need an Italian teaching qualification.
STATE_HIRING = re.compile(r"interpell|supplenz|\bGPS\b|\bM\.?A\.?D\.?\b|messa\s+a\s+disposizione|immissioni?\s+in\s+ruolo", re.I)
NOT_TEACHING = re.compile(
    r"sostegno|\bATA\b|collaborator\w+\s+scolastic|assistent\w+\s+(amministrativ|tecnic)|\bDSGA\b|"
    r"infermier|cuoc[oh]|psicolog|medico\s+competente|\bRSPP\b|\bDPO\b|fornitur|acquist|noleggio|"
    r"viaggi?\s+d'?istruzione|trasporto|pullman|assicurazion",
    re.I,
)
# A call that has already been decided: still useful as a sign that the school hires English experts.
OUTCOME = re.compile(
    r"^\W*(decreto|nomina|graduatori|esito|esiti|verbale|aggiudica|affidament|individuazione|revoca|"
    r"presa\s+d'?atto|contratto|conferimento|stipula)|graduatori[ae]|\besit[oi]\b|aggiudicazion|"
    r"affidamento|commissione\s+(di\s+)?valutazion|\bnomina\b|conferimento\s+(dell?'?\s*)?incaric",
    re.I,
)
PAPERWORK = re.compile(
    r"^\W*determin[ae]|\bmodell[oi]\b|\bmodul[oi]\b|fac[\s-]?simile|informativa|privacy|albo\s+sindacale|"
    r"sciopero|libri\s+di\s+testo|circolare\s+n|\bcirc\.\s|calendario|orario\s+delle\s+lezioni|iscrizion|"
    r"insussistenza|conflitt\w+\s+(di\s+)?interess|incompatibilit|dichiarazion|attestazion|\bRUP\b|"
    r"decisione\s+a\s+contrarre|inizio\s+(del\s+)?cors|pet\s+therapy|curriculum\s+vitae|\bCV\b",
    re.I,
)
# Calls only for the school's own staff.
INTERNAL_ONLY = re.compile(r"\binter(e)?n[oaie]\b", re.I)  # also the common typo "intereno"
EXTERNAL = re.compile(r"\bestern[oaie]\b|pubblic[oa]", re.I)


def clean_title(title):
    """'sigillo_AVVISO selezione TUTOR-signed.pdf (438.08 KB)' -> 'AVVISO selezione TUTOR'."""
    t = " ".join((title or "").split())
    t = re.sub(r"^\W*\d{1,3}\)\s*", "", t)
    t = re.sub(r"^(sigillo|timbro|firmato|signed)_+", "", t, flags=re.I)
    t = re.sub(r"\s*\(\d+([.,]\d+)?\s*[KMG]B\)\s*$", "", t, flags=re.I)
    t = re.sub(r"(\.(pdf|docx?|odt|p7m))+(\s*\(\d+\))?(\.(pdf|p7m))*\s*$", "", t, flags=re.I)
    t = re.sub(r"[-_]signed$", "", t, flags=re.I)
    t = t.replace("_", " ")
    return " ".join(t.split())


def classify(title, text=""):
    """Return "open", "filled" or None for a notice."""
    t = clean_title(title)
    if not t:
        return None
    body = f"{t} {text or ''}"
    if STATE_HIRING.search(t) or NOT_TEACHING.search(t) or PAPERWORK.search(t):
        return None
    if INTERNAL_ONLY.search(t) and not EXTERNAL.search(t):
        return None
    # Tutors are the school's own teachers; keep only if the notice also wants an outside expert.
    if re.search(r"\btutor\b", t, re.I) and not re.search(r"espert|madre\s?-?lingua|estern", t, re.I):
        return None
    en_title = bool(ENGLISH.search(t))
    if not (en_title or ENGLISH.search(body)):
        return None
    if OTHER_LANGUAGE.search(t) and not en_title:
        return None
    if not (CONTRACT.search(t) or (en_title and CONTRACT.search(body))):
        return None
    # "Avviso per il conferimento di incarico..." is still an open call; an outcome says so up front
    # or names a ranking, panel or award.
    if re.match(r"\W*(avviso|bando|manifestazione|ricerca|selezione|call|cercasi|richiesta)\b", t, re.I) and not re.search(
            r"\besit|graduatoria\s+(provvisoria|definitiva)|aggiudica|commissione|rettifica\s+graduatoria", t, re.I):
        return "open"
    # "Decreto di avvio della selezione" opens a procedure.
    if re.match(r"\W*decreto\s+(di|per)\s+(l['’]\s*)?avvio", t, re.I):
        return "open"
    if re.match(r"\W*incaric", t, re.I):
        return "filled"  # an assignment to a named person
    return "filled" if OUTCOME.search(t) else "open"


MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7,
    "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}
_NUM_DATE = re.compile(r"\b(\d{1,2})\s?[/.\-]\s?(\d{1,2})\s?[/.\-]\s?(\d{4}|\d{2})\b")
_WORD_DATE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", re.I)


def _mk(d, m, y):
    y = int(y)
    if y < 100:
        y += 2000
    try:
        return dt.date(y, int(m), int(d))
    except ValueError:
        return None


def find_dates(s):
    out = []
    for m in _NUM_DATE.finditer(s or ""):
        d = _mk(*m.groups())
        if d:
            out.append((m.start(), d))
    for m in _WORD_DATE.finditer(s or ""):
        d = _mk(m.group(1), MONTHS[m.group(2).lower()], m.group(3))
        if d:
            out.append((m.start(), d))
    return [d for _, d in sorted(out)]


def parse_date(s):
    """First date in a string: 21-04-2026, 02/11/2026, 2026-11-02T.., 'Mon, 2 Nov 2026 ..'."""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return _mk(m.group(3), m.group(2), m.group(1))
    m = re.search(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+(\d{4})", s)
    if m:
        mon = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].index(m.group(2).lower()[:3]) + 1
        return _mk(m.group(1), mon, m.group(3))
    found = find_dates(s)
    return found[0] if found else None


DEADLINE_CUE = re.compile(
    r"entro\s+(e\s+non\s+oltre\s+)?|scadenz\w*|termine\s+(ultimo\s+)?(per|di)|non\s+oltre|fino\s+al(le)?\b",
    re.I,
)


def find_deadline(text, today):
    """First plausible date after a deadline cue ('entro le ore 12 del 5/11/2026')."""
    if not text:
        return None
    for cue in DEADLINE_CUE.finditer(text):
        for d in find_dates(text[cue.end(): cue.end() + 90]):
            if today - dt.timedelta(days=90) <= d <= today + dt.timedelta(days=240):
                return d
    return None


def date_from_url(url):
    m = re.search(r"/(20\d{2})/(\d{1,2})/(?:(\d{1,2})/)?", url or "")
    if m:
        return _mk(m.group(3) or 1, m.group(2), m.group(1))
    return None
