"""
edgedash.agents.mock_fetcher
============================
Simulates a real job-listing fetch without any network calls.

Guarantee for deduplication testing
-------------------------------------
Listings with IDs "STABLE-001" through "STABLE-004" are identical on every run.
The remaining 8 listings carry run-time-generated IDs so they look "new" each
cycle.  On the second run the Fetcher should report 8 new + 4 already-known.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from edgedash.agents.base import Agent, AgentResult

if TYPE_CHECKING:
    from edgedash.config import Config
    from edgedash.storage import Storage

# ---------------------------------------------------------------------------
# Static fixture data — 4 stable listings (same id every run)
# ---------------------------------------------------------------------------

_STABLE_LISTINGS: list[dict] = [
    {
        "id": "STABLE-001",
        "title": "Senior Data Engineer",
        "company": "Stripe",
        "location": "San Francisco, CA",
        "seniority": "Senior",
        "description": (
            "Build and maintain large-scale data pipelines using Apache Spark, "
            "dbt, and Airflow. Own the lakehouse migration from Redshift to "
            "Snowflake. Strong Python and SQL required; experience with Kafka "
            "and streaming architectures a plus."
        ),
        "skills": ["Python", "SQL", "Apache Spark", "dbt", "Airflow", "Snowflake", "Kafka"],
        "posted_at": "2026-08-10T09:00:00Z",
    },
    {
        "id": "STABLE-002",
        "title": "ML Engineer",
        "company": "Cohere",
        "location": "Toronto, ON",
        "seniority": "Mid-level",
        "description": (
            "Train, fine-tune, and serve large language models in production. "
            "Collaborate with research to turn experiments into scalable APIs. "
            "Proficiency in PyTorch, Triton, and distributed training required. "
            "Familiarity with RLHF pipelines strongly preferred."
        ),
        "skills": ["Python", "PyTorch", "Triton", "RLHF", "CUDA", "Kubernetes"],
        "posted_at": "2026-08-11T14:30:00Z",
    },
    {
        "id": "STABLE-003",
        "title": "Data Scientist — Growth",
        "company": "Shopify",
        "location": "Ottawa, ON",
        "seniority": "Mid-level",
        "description": (
            "Partner with product teams to instrument experiments, build causal "
            "models, and surface actionable insights. Heavy use of Python, "
            "statsmodels, and internal A/B tooling. SQL fluency expected; "
            "experience with causal inference or Bayesian methods a strong plus."
        ),
        "skills": ["Python", "SQL", "statsmodels", "A/B Testing", "Causal Inference", "Tableau"],
        "posted_at": "2026-08-09T11:00:00Z",
    },
    {
        "id": "STABLE-004",
        "title": "Analytics Engineer",
        "company": "Notion",
        "location": "New York, NY",
        "seniority": "Mid-level",
        "description": (
            "Own the semantic layer: model raw Fivetran/Airbyte data in dbt, "
            "write data quality tests, and maintain the metric catalog in "
            "Metabase. Strong SQL and dbt required; Python scripting a plus."
        ),
        "skills": ["SQL", "dbt", "Fivetran", "Airbyte", "Metabase", "Python"],
        "posted_at": "2026-08-12T08:45:00Z",
    },
]

# ---------------------------------------------------------------------------
# Dynamic fixture templates — 8 listings generated fresh each run
# ---------------------------------------------------------------------------

_DYNAMIC_TEMPLATES: list[dict] = [
    {
        "title": "Staff Data Engineer",
        "company": "Databricks",
        "location": "{city}",
        "seniority": "Staff",
        "description": (
            "Lead the design of multi-tenant Lakehouse architectures on Delta Lake. "
            "Drive adoption of Spark Structured Streaming and Unity Catalog. "
            "Mentor junior engineers and collaborate with product on data platform "
            "strategy. Python, Scala, and Terraform required."
        ),
        "skills": ["Python", "Scala", "Apache Spark", "Delta Lake", "Terraform", "Unity Catalog"],
    },
    {
        "title": "Senior Machine Learning Engineer",
        "company": "Waymo",
        "location": "{city}",
        "seniority": "Senior",
        "description": (
            "Develop perception models for autonomous vehicles. Work with "
            "point-cloud and camera data using PyTorch and ONNX. Deploy models "
            "with sub-10ms inference latency targets. C++ familiarity helpful."
        ),
        "skills": ["Python", "PyTorch", "ONNX", "C++", "CUDA", "MLflow"],
    },
    {
        "title": "Data Engineer — Real-Time",
        "company": "DoorDash",
        "location": "{city}",
        "seniority": "Mid-level",
        "description": (
            "Build real-time feature pipelines feeding the recommendation engine. "
            "Stack: Flink, Kafka, and Python. Own SLAs for sub-second feature "
            "freshness. Prior experience with stream processing required."
        ),
        "skills": ["Python", "Apache Flink", "Kafka", "Redis", "SQL"],
    },
    {
        "title": "Principal Data Scientist",
        "company": "Spotify",
        "location": "{city}",
        "seniority": "Principal",
        "description": (
            "Set the technical vision for personalisation ML across 600M users. "
            "Collaborate with platform and product to move models from research "
            "to production. Deep expertise in recommendation systems and Python "
            "required; publications in top venues preferred."
        ),
        "skills": ["Python", "Recommendation Systems", "Collaborative Filtering", "Spark", "MLflow"],
    },
    {
        "title": "Junior Data Analyst",
        "company": "HubSpot",
        "location": "{city}",
        "seniority": "Junior",
        "description": (
            "Support the revenue analytics team with dashboards, ad-hoc SQL "
            "queries, and monthly reporting. Looker and SQL required; Python "
            "a strong plus for automation tasks."
        ),
        "skills": ["SQL", "Looker", "Python", "Excel", "Google Sheets"],
    },
    {
        "title": "Senior Analytics Engineer",
        "company": "Figma",
        "location": "{city}",
        "seniority": "Senior",
        "description": (
            "Expand the data model powering Figma's self-serve analytics. Own "
            "dbt model design, CI pipelines, and documentation. Work closely "
            "with data science to serve reliable, well-tested datasets. "
            "Strong dbt and SQL required; Python familiarity expected."
        ),
        "skills": ["dbt", "SQL", "Python", "Airflow", "Data Quality"],
    },
    {
        "title": "Applied Scientist — NLP",
        "company": "Amazon",
        "location": "{city}",
        "seniority": "Senior",
        "description": (
            "Research and productionise NLP models for Alexa. Requires deep "
            "knowledge of transformers, fine-tuning, and evaluation methodology. "
            "PyTorch and AWS SageMaker stack. PhD or equivalent research "
            "experience expected."
        ),
        "skills": ["Python", "PyTorch", "NLP", "Transformers", "AWS SageMaker", "HuggingFace"],
    },
    {
        "title": "Data Platform Engineer",
        "company": "Plaid",
        "location": "{city}",
        "seniority": "Mid-level",
        "description": (
            "Maintain and evolve Plaid's internal data platform: ingestion, "
            "transformation, and observability. Stack includes Airbyte, dbt, "
            "Great Expectations, and Airflow. Python and SQL fluency required."
        ),
        "skills": ["Python", "SQL", "Airbyte", "dbt", "Airflow", "Great Expectations"],
    },
]


def _make_dynamic_listing(template: dict, city: str) -> dict:
    """Stamp a dynamic listing with a fresh UUID and today's timestamp."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": f"DYN-{uuid.uuid4().hex[:8].upper()}",
        "title": template["title"],
        "company": template["company"],
        "location": template["location"].format(city=city),
        "seniority": template["seniority"],
        "description": template["description"],
        "skills": list(template["skills"]),
        "posted_at": now,
    }


# ---------------------------------------------------------------------------
# Agent implementation
# ---------------------------------------------------------------------------

class MockFetcher(Agent):
    """
    Simulates fetching job listings without real network calls.

    Goal        : Populate storage with fresh job listings.
    Stop cond.  : All configured sources have been queried (here: one mock source).
    """

    @property
    def name(self) -> str:
        return "MockFetcher"

    def run(self, config: "Config", storage: "Storage") -> AgentResult:
        city = getattr(config, "city", "Remote")

        # Build the full 12-listing set
        dynamic = [
            _make_dynamic_listing(t, city)
            for t in _DYNAMIC_TEMPLATES
        ]
        all_listings = _STABLE_LISTINGS + dynamic  # 4 stable + 8 dynamic = 12

        new_count = storage.upsert_listings(all_listings)

        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=new_count,
            notes=(
                f"Generated {len(all_listings)} listings "
                f"({len(_STABLE_LISTINGS)} stable, {len(dynamic)} dynamic). "
                f"{new_count} were new or updated in storage."
            ),
        )
