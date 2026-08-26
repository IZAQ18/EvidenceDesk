from __future__ import annotations

import json
from pathlib import Path


def test_aws_data_template_has_private_recoverable_defaults():
    template_path = Path("infra/aws/evidencedesk-data.json")
    template = json.loads(template_path.read_text(encoding="utf-8"))
    resources = template["Resources"]

    bucket = resources["OriginalsBucket"]
    assert bucket["DeletionPolicy"] == "Retain"
    assert bucket["Properties"]["VersioningConfiguration"]["Status"] == "Enabled"
    assert all(
        bucket["Properties"]["PublicAccessBlockConfiguration"].values()
    )
    encryption = bucket["Properties"]["BucketEncryption"]
    assert encryption["ServerSideEncryptionConfiguration"][0][
        "ServerSideEncryptionByDefault"
    ]["SSEAlgorithm"] == "aws:kms"
    lifecycle_rules = bucket["Properties"]["LifecycleConfiguration"]["Rules"]
    assert any("AbortIncompleteMultipartUpload" in rule for rule in lifecycle_rules)

    database = resources["Database"]
    properties = database["Properties"]
    assert properties["PubliclyAccessible"] is False
    assert properties["StorageEncrypted"] is True
    assert properties["ManageMasterUserPassword"] is True
    assert properties["BackupRetentionPeriod"] == {"Ref": "BackupRetentionDays"}
    assert properties["DeleteAutomatedBackups"] is False
    assert properties["EnableCloudwatchLogsExports"] == ["postgresql", "upgrade"]
    assert database["DeletionPolicy"] == "Snapshot"

    role_statements = resources["ApplicationTaskRole"]["Properties"]["Policies"][0][
        "PolicyDocument"
    ]["Statement"]
    actions = {
        action
        for statement in role_statements
        for action in (
            statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        )
    }
    assert "s3:PutObject" in actions
    assert "secretsmanager:GetSecretValue" in actions
    assert "kms:Decrypt" in actions
    secret_statement = next(
        statement
        for statement in role_statements
        if statement["Sid"] == "ReadApplicationDatabaseSecret"
    )
    assert secret_statement["Resource"] == {"Ref": "ApplicationDatabaseSecret"}
    assert "MasterUserSecret" not in json.dumps(secret_statement)
