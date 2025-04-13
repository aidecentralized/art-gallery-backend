from rest_framework import serializers
from .models import VerificationRequest, VerificationCheck, HealthCheck

class VerificationCheckSerializer(serializers.ModelSerializer):
    """Serializer for verification checks."""
    class Meta:
        model = VerificationCheck
        fields = ['check_type', 'status', 'details', 'message', 'created_at', 'updated_at']
        read_only_fields = fields

class VerificationRequestSerializer(serializers.ModelSerializer):
    """Serializer for verification requests."""
    server_name = serializers.CharField(source='server.name', read_only=True)

    class Meta:
        model = VerificationRequest
        fields = [
            'id', 'server', 'server_name', 'status', 'verification_token',
            'verification_method', 'created_at', 'updated_at', 'completed_at'
        ]
        read_only_fields = [
            'id', 'server_name', 'verification_token', 'created_at',
            'updated_at', 'completed_at'
        ]

    def create(self, validated_data):
        """Create a verification request and generate a verification token."""
        import secrets
        from django.utils import timezone
        from django.conf import settings

        # Generate token and expiry before saving
        verification_token = secrets.token_urlsafe(32)
        verification_token_expiry = timezone.now() + settings.VERIFICATION_TOKEN_EXPIRY

        # Add token and expiry to validated data
        validated_data['verification_token'] = verification_token
        validated_data['verification_token_expiry'] = verification_token_expiry

        verification_request = VerificationRequest.objects.create(**validated_data)
        verification_request.generate_verification_token()

        # Create initial verification checks
        VerificationCheck.objects.create(
            verification_request=verification_request,
            check_type='ownership',
            status='pending'
        )
        VerificationCheck.objects.create(
            verification_request=verification_request,
            check_type='health',
            status='pending'
        )
        VerificationCheck.objects.create(
            verification_request=verification_request,
            check_type='capabilities',
            status='pending'
        )
        VerificationCheck.objects.create(
            verification_request=verification_request,
            check_type='security',
            status='pending'
        )

        return verification_request

class VerificationStatusSerializer(serializers.ModelSerializer):
    """Serializer for checking verification status."""
    server_name = serializers.CharField(source='server.name', read_only=True)
    checks = VerificationCheckSerializer(many=True, read_only=True)
    next_steps = serializers.SerializerMethodField()

    class Meta:
        model = VerificationRequest
        fields = [
            'id', 'server', 'server_name', 'status', 'created_at',
            'updated_at', 'checks', 'next_steps'
        ]
        read_only_fields = fields

    def get_next_steps(self, obj):
        """Get instructions for the next steps in verification."""
        if obj.status == 'pending':
            return (
                "To verify ownership of your server, please choose one of the verification methods "
                "and provide the required proof. You can verify by adding a DNS TXT record, "
                "uploading a verification file to your server, or adding a meta tag to your server's homepage."
            )
        elif obj.status == 'in_progress':
            return (
                "We are currently verifying your server. This process usually takes a few minutes, "
                "but can take up to 24 hours in some cases. You will be notified once the verification is complete."
            )
        elif obj.status == 'completed':
            return "Your server has been successfully verified. No further action is required."
        else:  # failed
            return (
                "Verification failed. Please review the check results for details on what went wrong, "
                "make the necessary corrections, and try again."
            )

class VerificationCompletionSerializer(serializers.Serializer):
    """Serializer for completing verification."""
    verification_method = serializers.ChoiceField(
        choices=VerificationRequest.VERIFICATION_METHOD_CHOICES,
        required=True
    )
    verification_proof = serializers.CharField(required=False, allow_blank=True, default="")

class VerificationResultSerializer(serializers.ModelSerializer):
    """Serializer for verification results."""
    server_name = serializers.CharField(source='server.name', read_only=True)
    verification_details = serializers.SerializerMethodField()
    badge_url = serializers.SerializerMethodField()

    class Meta:
        model = VerificationRequest
        fields = [
            'id', 'server', 'server_name', 'status', 'created_at',
            'completed_at', 'verification_details', 'badge_url'
        ]
        read_only_fields = fields

    def get_verification_details(self, obj):
        """Get details of the verification process."""
        checks = obj.checks.all()
        return {
            'method': obj.verification_method,
            'checks_passed': [check.check_type for check in checks if check.status == 'passed'],
            'is_verified': obj.server.verified
        }

    def get_badge_url(self, obj):
        """Get URL for the verification badge."""
        if obj.server.verified:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(f'/api/v1/verification/badge/{obj.server.id}/')
        return None

class HealthCheckSerializer(serializers.ModelSerializer):
    """Serializer for health checks."""
    server_name = serializers.CharField(source='server.name', read_only=True)

    class Meta:
        model = HealthCheck
        fields = [
            'id', 'server', 'server_name', 'is_up', 'response_time',
            'status_code', 'error_message', 'details', 'created_at'
        ]
        read_only_fields = ['id', 'server_name', 'created_at']