"""Bedrock tool_use schema definitions for DevOps infrastructure queries."""

DEVOPS_TOOLS = [
    {
        "toolSpec": {
            "name": "kubectl_exec",
            "description": "Execute a kubectl command against the EKS cluster. Returns stdout/stderr. Use for querying deployments, pods, services, configmaps, logs, etc.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Full kubectl command (e.g. 'kubectl get pods -n dockercoins -o wide')"
                        }
                    },
                    "required": ["command"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "aws_cli_exec",
            "description": "Execute an AWS CLI command. Returns stdout/stderr. Use for querying CloudWatch alarms, metrics, logs, EKS info, etc.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Full AWS CLI command (e.g. 'aws cloudwatch describe-alarms --alarm-names my-alarm --region us-east-1')"
                        }
                    },
                    "required": ["command"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "read_file",
            "description": "Read the contents of a file from the project directory. Use for reading scenario definitions, SKILL.md, failure_modes.py, etc.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path from the project root (e.g. 'simulator/engine/failure_modes.py')"
                        }
                    },
                    "required": ["path"]
                }
            }
        }
    },
]
