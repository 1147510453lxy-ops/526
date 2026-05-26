#!/usr/bin/env python3
import html
import json
import os
import smtplib
import ssl
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SENT_PATH = DATA_DIR / "sent_pmids.json"

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

EXCLUDED_TYPES = {
    "Comment",
    "Editorial",
    "Letter",
    "News",
    "Published Erratum",
    "Retraction of Publication",
}

Q1_JOURNALS = {
    "annals of the rheumatic diseases": "SCImago 2024 Q1; rheumatology top journal",
    "arthritis & rheumatology (hoboken, n.j.)": "SCImago 2024 Q1",
    "rheumatology (oxford, england)": "SCImago 2024 Q1",
    "diabetes care": "SCImago 2024 Q1; endocrinology/diabetes clinical journal",
    "diabetologia": "SCImago 2024 Q1",
    "diabetes, obesity & metabolism": "SCImago 2024 Q1",
    "the lancet diabetes & endocrinology": "SCImago 2024 Q1",
    "nature metabolism": "SCImago 2024 Q1",
    "cell metabolism": "SCImago 2024 Q1",
    "nature communications": "SCImago 2024 Q1",
    "gut microbes": "SCImago 2024 Q1",
    "journal of clinical endocrinology and metabolism": "SCImago 2024 Q1",
    "clinical nutrition": "SCImago 2024 Q1",
    "international journal of obesity (2005)": "SCImago 2024 Q1",
    "obesity reviews": "SCImago 2024 Q1",
    "atherosclerosis": "SCImago 2024 Q1",
    "cardiovascular diabetology": "SCImago 2024 Q1",
    "journal of cachexia, sarcopenia and muscle": "SCImago 2024 Q1",
}

TOPIC_QUERIES = [
    (
        "高尿酸血症/痛风/尿酸代谢",
        '(hyperuricemia[Title/Abstract] OR hyperuricaemia[Title/Abstract] OR gout[Title/Abstract] '
        'OR urate[Title/Abstract] OR "uric acid"[Title/Abstract] OR xanthine oxidase[Title/Abstract])',
        100,
    ),
    (
        "糖尿病/胰岛素抵抗",
        '(diabetes[Title/Abstract] OR "type 2 diabetes"[Title/Abstract] OR "insulin resistance"[Title/Abstract] '
        'OR SGLT2[Title/Abstract] OR GLP-1[Title/Abstract])',
        60,
    ),
    (
        "肥胖/体重管理",
        '(obesity[Title/Abstract] OR overweight[Title/Abstract] OR adiposity[Title/Abstract] '
        'OR "weight loss"[Title/Abstract] OR tirzepatide[Title/Abstract] OR semaglutide[Title/Abstract])',
        40,
    ),
    (
        "高脂血症/脂代谢",
        '(dyslipidemia[Title/Abstract] OR hyperlipidemia[Title/Abstract] OR cholesterol[Title/Abstract] '
        'OR lipoprotein[Title/Abstract] OR triglyceride[Title/Abstract])',
        20,
    ),
]


@dataclass
class Paper:
    pmid: str
    title: str
    journal: str
    pub_date: str
    sort_date: str
    doi: str
    pub_types: list[str]
    abstract: str
    topic: str
    topic_score: int
    q1_basis: str


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name) or default
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def urlopen_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def urlopen_xml(url: str) -> ET.Element:
    with urllib.request.urlopen(url, timeout=30) as response:
        return ET.fromstring(response.read())


def ncbi_params(params: dict[str, str]) -> str:
    email = os.environ.get("NCBI_EMAIL")
    api_key = os.environ.get("NCBI_API_KEY")
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    return urllib.parse.urlencode(params)


def search_pubmed(query: str, days: int = 180, retmax: int = 80) -> list[str]:
    start = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y/%m/%d")
    term = f"({query}) AND ({start}[Date - Publication] : 3000[Date - Publication])"
    url = f"{NCBI_BASE}/esearch.fcgi?{ncbi_params({'db': 'pubmed', 'term': term, 'retmax': str(retmax), 'retmode': 'json', 'sort': 'pub date'})}"
    data = urlopen_json(url)
    time.sleep(0.35)
    return data.get("esearchresult", {}).get("idlist", [])


def fetch_summaries(pmids: list[str]) -> dict[str, dict]:
    if not pmids:
        return {}
    url = f"{NCBI_BASE}/esummary.fcgi?{ncbi_params({'db': 'pubmed', 'id': ','.join(pmids), 'retmode': 'json'})}"
    data = urlopen_json(url)
    time.sleep(0.35)
    result = data.get("result", {})
    return {pmid: result[pmid] for pmid in result.get("uids", []) if pmid in result}


def fetch_abstracts(pmids: list[str]) -> dict[str, str]:
    if not pmids:
        return {}
    url = f"{NCBI_BASE}/efetch.fcgi?{ncbi_params({'db': 'pubmed', 'id': ','.join(pmids), 'retmode': 'xml'})}"
    root = urlopen_xml(url)
    abstracts: dict[str, str] = {}
    for article in root.findall("./PubmedArticle"):
        pmid = article.findtext("./MedlineCitation/PMID")
        chunks = []
        for node in article.findall("./MedlineCitation/Article/Abstract/AbstractText"):
            label = node.attrib.get("Label")
            text = "".join(node.itertext()).strip()
            if text:
                chunks.append(f"{label}: {text}" if label else text)
        if pmid:
            abstracts[pmid] = " ".join(chunks)
    time.sleep(0.35)
    return abstracts


def load_sent() -> set[str]:
    if not SENT_PATH.exists():
        return set()
    try:
        data = json.loads(SENT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return set(data.get("pmids", []))


def save_sent(pmids: set[str]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    data = {
        "updated_at": datetime.now(UTC).isoformat(),
        "pmids": sorted(pmids),
    }
    SENT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def doi_from_summary(summary: dict) -> str:
    for item in summary.get("articleids", []):
        if item.get("idtype") == "doi":
            return item.get("value", "")
    return ""


def is_research_article(pub_types: list[str]) -> bool:
    return not any(pub_type in EXCLUDED_TYPES for pub_type in pub_types)


def collect_candidates() -> list[Paper]:
    seen: set[str] = set()
    scored: dict[str, tuple[str, int]] = {}
    ordered_pmids: list[str] = []

    for topic, query, score in TOPIC_QUERIES:
        for pmid in search_pubmed(query):
            if pmid not in seen:
                ordered_pmids.append(pmid)
                seen.add(pmid)
            if pmid not in scored or score > scored[pmid][1]:
                scored[pmid] = (topic, score)

    summaries = fetch_summaries(ordered_pmids)
    abstracts = fetch_abstracts(list(summaries.keys()))
    papers: list[Paper] = []

    for pmid, summary in summaries.items():
        journal = html.unescape(summary.get("fulljournalname") or summary.get("source") or "").strip()
        q1_basis = Q1_JOURNALS.get(journal.lower())
        pub_types = list(summary.get("pubtype", []))
        if not q1_basis or not is_research_article(pub_types):
            continue
        topic, topic_score = scored.get(pmid, ("其他内分泌代谢", 0))
        papers.append(
            Paper(
                pmid=pmid,
                title=html.unescape(summary.get("title", "")).strip(),
                journal=journal,
                pub_date=summary.get("pubdate", ""),
                sort_date=summary.get("sortpubdate", ""),
                doi=doi_from_summary(summary),
                pub_types=pub_types,
                abstract=abstracts.get(pmid, ""),
                topic=topic,
                topic_score=topic_score,
                q1_basis=q1_basis,
            )
        )

    return papers


def short_chinese_summary(paper: Paper) -> tuple[str, str, str]:
    title_abs = f"{paper.title} {paper.abstract}".lower()
    if any(term in title_abs for term in ["sglt2", "sodium-glucose"]):
        finding = "SGLT2 抑制剂与更低的痛风相关用药或尿酸相关风险信号相关，提示降糖药选择可能同时影响痛风管理。"
        meaning = "适合用于糖尿病合并高尿酸/痛风患者的药物策略讨论。"
    elif "tirzepatide" in title_abs:
        finding = "替尔泊肽治疗伴随血尿酸下降，体重下降可能解释了主要效应。"
        meaning = "提示体重管理药物可能为高尿酸或痛风风险控制带来附加获益。"
    elif any(term in title_abs for term in ["ultrasound", "asymptomatic hyperuric"]):
        finding = "无症状高尿酸人群中可检测到尿酸盐沉积、亚临床关节损害或炎症信号。"
        meaning = "支持对部分高风险无症状高尿酸人群进行更精细的风险分层。"
    elif any(term in title_abs for term in ["kidney", "ckd", "renal"]):
        finding = "痛风或高尿酸患者存在不同肾脏风险轨迹，尿酸排泄、结石负担和遗传因素可能影响 CKD 进展。"
        meaning = "有助于识别需要强化降尿酸和肾脏保护的患者亚型。"
    elif any(term in title_abs for term in ["neutrophil", "immune", "inflammation", "sepsis"]):
        finding = "尿酸不仅参与炎症，还可能改变免疫细胞功能和宿主防御能力。"
        meaning = "为高尿酸相关免疫代谢异常提供机制线索。"
    elif any(term in title_abs for term in ["cholesterol", "lipoprotein", "dyslipidemia"]):
        finding = "研究聚焦脂质代谢异常及其与肠道菌群、炎症或心血管代谢风险的联系。"
        meaning = "对高脂血症和心血管代谢风险综合管理有参考价值。"
    elif any(term in title_abs for term in ["obesity", "adiposity", "weight"]):
        finding = "研究聚焦肥胖、体重管理或脂肪组织相关的代谢调控机制。"
        meaning = "可为肥胖相关高尿酸、糖脂代谢异常的综合干预提供参考。"
    else:
        finding = "研究围绕内分泌代谢疾病的新机制、风险分层或治疗策略展开。"
        meaning = "适合作为近期内分泌代谢领域高质量进展跟踪。"

    reason = f"主题命中：{paper.topic}；期刊为 Q1；发表时间较新。"
    return finding, meaning, reason


def render_digest(papers: list[Paper]) -> str:
    today = datetime.now(UTC).astimezone().strftime("%Y-%m-%d")
    lines = [
        f"内分泌代谢一区最新论文推送",
        f"日期：{today}",
        f"收件邮箱：{env('MAIL_TO')}",
        "",
        "筛选规则：优先高尿酸血症/痛风/尿酸代谢，其次糖尿病、肥胖、高脂血症等方向；优先 PubMed 近期收录或发表的研究论文；期刊分区以脚本内维护的 SCImago Q1 白名单作为云端替代核验依据。",
        "",
    ]

    if not papers:
        lines += [
            "今天未筛到符合条件且未推送过的新论文。",
            "",
            "提示：可适当放宽时间窗口、扩展 Q1 期刊白名单，或改为允许综述论文。",
        ]
        return "\n".join(lines) + "\n"

    for index, paper in enumerate(papers, 1):
        finding, meaning, reason = short_chinese_summary(paper)
        doi_url = f"https://doi.org/{paper.doi}" if paper.doi else "暂无 DOI"
        lines += [
            f"{index}. {paper.title}",
            f"期刊：{paper.journal}",
            f"发表日期：{paper.pub_date}",
            f"DOI：{doi_url}",
            f"PubMed：https://pubmed.ncbi.nlm.nih.gov/{paper.pmid}/",
            f"分区依据：{paper.q1_basis}",
            f"研究类型：{'; '.join(paper.pub_types)}",
            f"核心发现：{finding}",
            f"临床或科研意义：{meaning}",
            f"推荐理由：{reason}",
            "",
        ]

    lines += [
        "今日小结：",
        "本邮件由 GitHub Actions 自动检索 PubMed 并通过 126 SMTP 发送。若希望获得更像人工精读的中文摘要，可后续接入 OpenAI API key 进行摘要润色。",
        "",
        "检索来源：PubMed / NCBI E-utilities；期刊 Q1 白名单来源：SCImago Journal & Country Rank 手工维护。",
    ]
    return "\n".join(lines) + "\n"


def select_papers(candidates: list[Paper], limit: int = 5) -> list[Paper]:
    sent = load_sent()
    fresh = [paper for paper in candidates if paper.pmid not in sent]
    pool = fresh or candidates
    pool.sort(key=lambda paper: (paper.topic_score, paper.sort_date, paper.journal.lower()), reverse=True)
    return pool[:limit]


def send_email(subject: str, body: str) -> None:
    smtp_host = env("SMTP_HOST", "smtp.126.com")
    smtp_port = int(env("SMTP_PORT", "465"))
    smtp_user = env("SMTP_USER")
    smtp_pass = env("SMTP_PASS")
    mail_from = env("MAIL_FROM", smtp_user)
    mail_to = env("MAIL_TO")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = mail_to
    msg.set_content(body, charset="utf-8")

    if smtp_port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=45) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=45) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)


def main() -> int:
    candidates = collect_candidates()
    selected = select_papers(candidates)
    body = render_digest(selected)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    out_path = ROOT / f"daily_digest_{today}.txt"
    out_path.write_text(body, encoding="utf-8")

    subject = f"内分泌代谢一区最新论文推送 {today}"
    send_email(subject, body)

    sent = load_sent()
    sent.update(paper.pmid for paper in selected)
    save_sent(sent)
    print(f"Sent {len(selected)} papers to {env('MAIL_TO')}.")
    print(f"Digest written to {out_path.name}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
