from rest_framework import serializers
from .models import Repository, ResearchSession, ToolCallLog, Finding


class ToolCallLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ToolCallLog
        fields = ["id", "tool_name", "input_args", "output_summary", "success", "called_at"]


class FindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Finding
        fields = ["id", "file_path", "note", "line_reference", "created_at"]


class ResearchSessionDetailSerializer(serializers.ModelSerializer):
    tool_calls = ToolCallLogSerializer(many=True, read_only=True)
    findings = FindingSerializer(many=True, read_only=True)
    repo_url = serializers.CharField(source="repo.url", read_only=True)
    repo_name = serializers.CharField(source="repo.name", read_only=True)

    class Meta:
        model = ResearchSession
        fields = [
            "id", "repo_url", "repo_name", "question", "final_answer",
            "status", "total_tokens_used", "error_message",
            "started_at", "completed_at", "tool_calls", "findings",
        ]


class ResearchSessionListSerializer(serializers.ModelSerializer):
    repo_url = serializers.CharField(source="repo.url", read_only=True)
    repo_name = serializers.CharField(source="repo.name", read_only=True)
    finding_count = serializers.IntegerField(source="findings.count", read_only=True)
    tool_call_count = serializers.IntegerField(source="tool_calls.count", read_only=True)

    class Meta:
        model = ResearchSession
        fields = [
            "id", "repo_url", "repo_name", "question", "status",
            "total_tokens_used", "started_at", "completed_at",
            "finding_count", "tool_call_count",
        ]


class RepositorySerializer(serializers.ModelSerializer):
    session_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Repository
        fields = ["id", "url", "name", "last_analyzed_at", "created_at", "session_count"]


class StartSessionSerializer(serializers.Serializer):
    repo_url = serializers.URLField()
    question = serializers.CharField(min_length=10, max_length=2000)

    def validate_repo_url(self, value):
        if "github.com" not in value:
            raise serializers.ValidationError("Only GitHub URLs are supported.")
        return value.rstrip("/")
