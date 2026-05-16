from django.db import models


class Repository(models.Model):
    url = models.URLField(unique=True)
    name = models.CharField(max_length=255)
    local_path = models.CharField(max_length=500, blank=True)
    last_analyzed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "repositories"

    def __str__(self):
        return self.name

    @property
    def session_count(self):
        return self.sessions.count()


STATUS_CHOICES = [
    ("pending", "Pending"),
    ("running", "Running"),
    ("completed", "Completed"),
    ("failed", "Failed"),
]


class ResearchSession(models.Model):
    repo = models.ForeignKey(
        Repository, on_delete=models.CASCADE, related_name="sessions"
    )
    question = models.TextField()
    final_answer = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    total_tokens_used = models.IntegerField(default=0)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"Session {self.id}: {self.question[:50]}"


class ToolCallLog(models.Model):
    session = models.ForeignKey(
        ResearchSession, on_delete=models.CASCADE, related_name="tool_calls"
    )
    tool_name = models.CharField(max_length=100)
    input_args = models.JSONField()
    output_summary = models.TextField()
    success = models.BooleanField(default=True)
    called_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["called_at"]

    def __str__(self):
        return f"{self.tool_name} @ session {self.session_id}"


class Finding(models.Model):
    session = models.ForeignKey(
        ResearchSession, on_delete=models.CASCADE, related_name="findings"
    )
    file_path = models.CharField(max_length=500)
    note = models.TextField()
    line_reference = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.file_path}: {self.note[:50]}"
