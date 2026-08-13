#!/usr/bin/env python3
"""
Create a detailed case study .docx with specific verifiable metrics
for RAG testing.
"""
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()

# Title
title = doc.add_heading("Case Study: FinNova Bank — QA Transformation with Agentic AI", level=0)
subtitle = doc.add_paragraph()
subtitle.add_run("Client engagement report | January 2025 – December 2025").italic = True
doc.add_paragraph()

# ============ EXECUTIVE SUMMARY ============
doc.add_heading("Executive Summary", level=1)
doc.add_paragraph(
    "Between January 2025 and December 2025, Alex Pavsky Consulting partnered with FinNova Bank "
    "(a mid-size digital banking platform serving 2.4 million retail customers) to modernize their "
    "quality assurance practice. The engagement deployed 7 specialized AI agents, rebuilt their "
    "Playwright + TypeScript test framework, and introduced LLM evaluation gates into their CI/CD "
    "pipeline. The 12-month program produced a 67% reduction in production bugs, recovered an "
    "estimated $2.4 million in annual operating costs, and reduced mean time to detection (MTTD) "
    "from 4.5 hours to 12 minutes."
)

# ============ CLIENT BACKGROUND ============
doc.add_heading("Client Background", level=1)
doc.add_paragraph(
    "FinNova Bank operates a multi-tenant SaaS banking platform headquartered in Charlotte, NC, "
    "with engineering hubs in Austin (TX) and Krakow (Poland). The platform handles approximately "
    "$48 billion in annual transaction volume across 2.4 million retail customers and 18,000 "
    "small-business accounts. Their primary stack is Node.js + GraphQL on the backend, React + "
    "TypeScript on the frontend, with PostgreSQL 15 and Kafka 3.6 for event streaming."
)
doc.add_paragraph(
    "Pre-engagement QA metrics (December 2024 baseline):"
)
for bullet in [
    "Production bug rate: 18.4 P1/P2 incidents per month",
    "Mean time to detection (MTTD): 4 hours 30 minutes",
    "Mean time to resolution (MTTR): 11 hours 15 minutes",
    "Test coverage (regression): 34%",
    "Manual QA hours per release: 312 hours",
    "CI/CD pipeline duration: 47 minutes",
    "QA team size: 14 engineers (8 manual, 6 automation)",
    "Release frequency: 1 release per 2 weeks",
]:
    doc.add_paragraph(bullet, style="List Bullet")

# ============ THE CHALLENGE ============
doc.add_heading("The Challenge", level=1)
doc.add_paragraph(
    "FinNova's primary pain points were release velocity, escaping production defects, and "
    "the disproportionate cost of regression testing. A specific incident in November 2024 — "
    "the so-called \"Black Friday wire transfer outage\" — caused 3 hours 22 minutes of degraded "
    "service, blocked roughly $14.7 million in transfers, and exposed the brittleness of their "
    "manual test suites. Root cause analysis identified that 6 of the 9 contributing defects had "
    "been technically caught in pre-prod but suppressed by a flaky test quarantine of 287 tests "
    "(48% flake rate at the worst-affected suite)."
)

# ============ OUR SOLUTION ============
doc.add_heading("Our Solution: Seven Specialized AI Agents", level=1)
doc.add_paragraph(
    "We designed and deployed an agentic test orchestration system with seven specialized agents, "
    "each responsible for a specific quality concern. Agents communicate via the Model Context "
    "Protocol (MCP) and share a centralized observability layer built on Langfuse + Arize Phoenix."
)

agents = [
    ("Agent 1 — Test Generator", "Generates Playwright + TypeScript tests from user stories. Produced 3,247 automated test cases over 12 months."),
    ("Agent 2 — Test Healer", "Auto-repairs flaky tests by analyzing failure traces. Reduced flake rate from 48% to 2.7%."),
    ("Agent 3 — RAG Quality Gate", "Validates LLM-powered customer support responses against the knowledge base. Achieved Ragas faithfulness score of 0.91."),
    ("Agent 4 — Red Team Bot", "Continuously probes the customer-facing AI assistant for prompt injection. Caught 47 high-severity vulnerabilities before production."),
    ("Agent 5 — Performance Sentinel", "Runs k6 load tests on every merge to main. Identified 23 performance regressions that would have escaped to production."),
    ("Agent 6 — Data Integrity Verifier", "Cross-checks GraphQL responses against PostgreSQL and Kafka events. Detected 14 silent data corruption bugs."),
    ("Agent 7 — Release Captain", "Aggregates signals from agents 1-6 and produces go/no-go release recommendations. Blocked 9 unsafe releases in 2025."),
]
for name, desc in agents:
    p = doc.add_paragraph()
    p.add_run(name + ": ").bold = True
    p.add_run(desc)

# ============ IMPLEMENTATION TIMELINE ============
doc.add_heading("Implementation Timeline", level=1)
phases = [
    ("Phase 1 (Jan–Feb 2025)", "Discovery and framework rebuild. Migrated 1,142 legacy Selenium tests to Playwright + TypeScript. Establish PostgreSQL test data isolation."),
    ("Phase 2 (Mar–May 2025)", "Deployed Agents 1, 2, and 5 (Test Generator, Test Healer, Performance Sentinel). Integrated with GitHub Actions CI."),
    ("Phase 3 (Jun–Aug 2025)", "Deployed Agents 3 and 4 (RAG Quality Gate, Red Team Bot) for AI feature testing. Launched LLM-as-a-judge eval suite with 1,847 test cases."),
    ("Phase 4 (Sep–Oct 2025)", "Deployed Agents 6 and 7 (Data Integrity Verifier, Release Captain). Wired distributed tracing through Langfuse."),
    ("Phase 5 (Nov–Dec 2025)", "Tuning, documentation, and handover to FinNova's internal team. Trained 14 engineers over a 6-week program."),
]
for phase, desc in phases:
    p = doc.add_paragraph()
    p.add_run(phase + ": ").bold = True
    p.add_run(desc)

# ============ RESULTS (key metrics) ============
doc.add_heading("Results — 12-Month Outcomes", level=1)
doc.add_paragraph("Compared against the December 2024 baseline:")

results_table = doc.add_table(rows=1, cols=3)
results_table.style = "Light Grid Accent 1"
hdr = results_table.rows[0].cells
hdr[0].text = "Metric"
hdr[1].text = "Before"
hdr[2].text = "After"

results = [
    ("Production P1/P2 bugs per month", "18.4", "6.1 (−67%)"),
    ("Mean time to detection (MTTD)", "4h 30m", "12 minutes (−96%)"),
    ("Mean time to resolution (MTTR)", "11h 15m", "2h 40m (−76%)"),
    ("Regression test coverage", "34%", "89% (+55 pts)"),
    ("Manual QA hours per release", "312", "47 (−85%)"),
    ("CI/CD pipeline duration", "47 minutes", "8 minutes (−83%)"),
    ("Flaky test rate", "48%", "2.7% (−94%)"),
    ("Release frequency", "1 / 2 weeks", "4.3 / week (+760%)"),
    ("Annual operating cost (QA)", "$3.6M", "$1.2M (−$2.4M)"),
    ("Customer-reported AI hallucinations", "127 / month", "8 / month (−94%)"),
]
for metric, before, after in results:
    row = results_table.add_row().cells
    row[0].text = metric
    row[1].text = before
    row[2].text = after

# ============ NOTABLE INCIDENTS PREVENTED ============
doc.add_heading("Notable Incidents Prevented", level=1)
doc.add_paragraph(
    "Several high-impact incidents were caught before reaching production:"
)
for inc in [
    "March 15, 2025 — Red Team Bot detected a prompt injection vector in the chatbot that could expose customer account balances. Patched within 4 hours.",
    "May 22, 2025 — Data Integrity Verifier caught a Kafka message ordering bug that would have caused duplicate ACH transfers worth approximately $890,000.",
    "July 8, 2025 — Performance Sentinel flagged a 340% latency regression in the loan application API introduced by an unrelated refactor.",
    "September 30, 2025 — RAG Quality Gate blocked deployment of a customer support model that was hallucinating mortgage rates with a faithfulness score of 0.42.",
    "November 11, 2025 — Release Captain blocked a release that had only 64% test coverage on a new wire-transfer feature; the missing tests later uncovered a routing-number validation bug.",
]:
    doc.add_paragraph(inc, style="List Bullet")

# ============ TECHNICAL STACK ============
doc.add_heading("Technical Stack Deployed", level=1)
stack = [
    ("Test Automation", "Playwright 1.47, TypeScript 5.4, Pytest 8.2 (Python adapter)"),
    ("LLM Evaluation", "Promptfoo 0.85, DeepEval 1.4, Ragas 0.2.1"),
    ("Observability", "Langfuse 2.50 (self-hosted), Arize Phoenix 4.21"),
    ("Performance", "k6 0.52, Grafana 11.2, Prometheus 2.54"),
    ("Vector Database", "Qdrant 1.11 for vector retrieval; PostgreSQL 16 for metadata"),
    ("Agent Framework", "LangGraph 0.2, Model Context Protocol (MCP) 0.4"),
    ("CI/CD", "GitHub Actions, ArgoCD 2.12, Docker 26, Kubernetes 1.30"),
]
for category, tools in stack:
    p = doc.add_paragraph()
    p.add_run(category + ": ").bold = True
    p.add_run(tools)

# ============ LESSONS LEARNED ============
doc.add_heading("Lessons Learned", level=1)
doc.add_paragraph(
    "Three observations from the engagement that shaped subsequent client work:"
)
for lesson in [
    "Agent specialization beats general-purpose agents. Each of the 7 agents has a single responsibility and a tight feedback loop. Early attempts at a single multi-purpose agent showed 38% lower accuracy.",
    "RAG quality gates are non-negotiable for customer-facing AI. Without the Agent 3 + Ragas combination, customer-reported hallucinations would not have dropped 94%.",
    "Test isolation is upstream of everything. The 48% → 2.7% flake reduction was 80% attributable to PostgreSQL test data isolation, not better assertions.",
]:
    doc.add_paragraph(lesson, style="List Number")

# ============ ENGAGEMENT TEAM ============
doc.add_heading("Engagement Team", level=1)
doc.add_paragraph(
    "Lead consultant: Alex Pavsky (Senior QA Automation Engineer / SDET). "
    "Supporting team: 2 automation engineers from FinNova, 1 ML engineer from Anthropic Solutions Group. "
    "Engagement budget: $480,000 across 12 months. ROI realized within 5 months of completion."
)

# Save
out_path = "/tmp/finnova_case_study.docx"
doc.save(out_path)
print(f"Created: {out_path}")
print(f"Sections: {len(doc.paragraphs)} paragraphs, {len(doc.tables)} table")
