"""AWS FIS lifecycle: start, stop, status check."""

from typing import Optional

import boto3


def _get_client(region: str = "us-east-1"):
    return boto3.client("fis", region_name=region)


def start(template_id: str, region: str = "us-east-1", tags: dict = None) -> tuple:
    client = _get_client(region)
    params = {"experimentTemplateId": template_id}
    if tags:
        params["tags"] = tags

    try:
        resp = client.start_experiment(**params)
        experiment = resp["experiment"]
        return True, experiment["id"], experiment.get("state", {}).get("status", "")
    except Exception as e:
        return False, "", str(e)


def stop(experiment_id: str, region: str = "us-east-1") -> tuple:
    client = _get_client(region)
    try:
        resp = client.stop_experiment(id=experiment_id)
        return True, resp["experiment"].get("state", {}).get("status", ""), ""
    except Exception as e:
        return False, "", str(e)


def status(experiment_id: str, region: str = "us-east-1") -> Optional[str]:
    client = _get_client(region)
    try:
        resp = client.get_experiment(id=experiment_id)
        return resp["experiment"]["state"]["status"]
    except Exception:
        return None


def list_templates(region: str = "us-east-1", tags: dict = None) -> list:
    client = _get_client(region)
    try:
        resp = client.list_experiment_templates(maxResults=50)
        templates = resp.get("experimentTemplates", [])

        if tags:
            filtered = []
            for tmpl in templates:
                tmpl_tags = tmpl.get("tags", {})
                if all(tmpl_tags.get(k) == v for k, v in tags.items()):
                    filtered.append(tmpl)
            templates = filtered

        return [
            {
                "id": t["id"],
                "description": t.get("description", ""),
                "tags": t.get("tags", {}),
            }
            for t in templates
        ]
    except Exception:
        return []
