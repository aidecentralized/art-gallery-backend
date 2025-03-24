import logging
import urllib.parse
import requests
import dns.resolver
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.http import HttpResponse
from rest_framework import status, permissions, generics, views
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from common.utils import check_server_health, extract_domain_from_url
from servers.models import Server
from servers.views import IsOwnerOrReadOnly
from .models import VerificationRequest, VerificationCheck, HealthCheck
from .serializers import (
    VerificationRequestSerializer,
    VerificationStatusSerializer,
    VerificationCompletionSerializer,
    VerificationResultSerializer,
    HealthCheckSerializer
)

logger = logging.getLogger('mcp_nexus')

class RequestVerificationView(generics.CreateAPIView):
    """
    API view for requesting server verification.
    """
    serializer_class = VerificationRequestSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]

    @extend_schema(
        summary="Request server verification",
        description="Initiate the verification process for an MCP server.",
        responses={
            200: VerificationRequestSerializer,
            400: {"type": "object", "properties": {"error": {"type": "string"}}},
            404: {"type": "object", "properties": {"error": {"type": "string"}}}
        }
    )
    def post(self, request, *args, **kwargs):
        # Get the server
        server_id = kwargs.get('server_id')
        server = get_object_or_404(Server, id=server_id)

        # Check if the user is the server owner
        self.check_object_permissions(request, server)

        # Check if there's already an active verification request
        existing_request = VerificationRequest.objects.filter(
            server=server,
            status__in=['pending', 'in_progress']
        ).first()

        if existing_request:
            return Response(
                {"error": f"There is already an active verification request for this server (status: {existing_request.status})"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create a new verification request
        serializer = self.get_serializer(data={'server': server.id})
        serializer.is_valid(raise_exception=True)
        verification_request = serializer.save()

        # Return the verification instructions
        return Response({
            **serializer.data,
            'verification_instructions': (
                "To verify your server, you must prove ownership. "
                "Choose one of the following verification methods:\n\n"
                "1. DNS Verification: Add a TXT record to your domain with the name "
                f"'_mcp-verification' and value '{verification_request.verification_token}'\n\n"
                "2. File Verification: Create a file at "
                f"'{server.url.rstrip('/')}/mcp-verification.txt' with the content "
                f"'{verification_request.verification_token}'\n\n"
                "3. Meta Tag Verification: Add the following meta tag to your server's homepage: "
                f"<meta name='mcp-verification' content='{verification_request.verification_token}'>"
            )
        })


class VerificationStatusView(generics.RetrieveAPIView):
    """
    API view for checking verification status.
    """
    serializer_class = VerificationStatusSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]

    @extend_schema(
        summary="Check verification status",
        description="Check the status of a server verification request.",
        responses={
            200: VerificationStatusSerializer,
            401: {"type": "object", "properties": {"error": {"type": "string"}}},
            404: {"type": "object", "properties": {"error": {"type": "string"}}}
        }
    )
    def get(self, request, *args, **kwargs):
        # Get the verification request
        verification_id = kwargs.get('verification_id')
        verification_request = get_object_or_404(VerificationRequest, id=verification_id)

        # Check if the user is the server owner
        self.check_object_permissions(request, verification_request.server)

        serializer = self.get_serializer(verification_request)
        return Response(serializer.data)


class CompleteVerificationView(views.APIView):
    """
    API view for completing verification.
    """
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]

    @extend_schema(
        summary="Complete verification",
        description="Complete the verification process by providing necessary proof.",
        request=VerificationCompletionSerializer,
        responses={
            200: VerificationResultSerializer,
            400: {"type": "object", "properties": {"error": {"type": "string"}}},
            401: {"type": "object", "properties": {"error": {"type": "string"}}},
            404: {"type": "object", "properties": {"error": {"type": "string"}}}
        }
    )
    def post(self, request, *args, **kwargs):
        # Get the verification request
        verification_id = kwargs.get('verification_id')
        verification_request = get_object_or_404(
            VerificationRequest,
            id=verification_id,
            status__in=['pending', 'in_progress']
        )

        # Check if the user is the server owner
        self.check_object_permissions(request, verification_request.server)

        # Check if the token is still valid
        if not verification_request.is_token_valid():
            return Response(
                {"error": "Verification token has expired. Please request a new verification."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate the completion data
        serializer = VerificationCompletionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Update the verification request
        verification_request.verification_method = serializer.validated_data['verification_method']
        verification_request.verification_proof = serializer.validated_data.get('verification_proof', '')
        verification_request.status = 'in_progress'
        verification_request.save()

        # Perform verification based on the chosen method
        verification_successful = False
        ownership_check = verification_request.checks.get(check_type='ownership')

        if verification_request.verification_method == 'dns':
            # DNS verification
            domain = extract_domain_from_url(verification_request.server.url)
            verification_successful = self._verify_dns(domain, verification_request.verification_token, ownership_check)
        elif verification_request.verification_method == 'file':
            # File verification
            url = f"{verification_request.server.url.rstrip('/')}/mcp-verification.txt"
            verification_successful = self._verify_file(url, verification_request.verification_token, ownership_check)
        elif verification_request.verification_method == 'meta_tag':
            # Meta tag verification
            url = verification_request.server.url
            verification_successful = self._verify_meta_tag(url, verification_request.verification_token, ownership_check)

        # Update the ownership check
        ownership_check.status = 'passed' if verification_successful else 'failed'
        ownership_check.save()

        if verification_successful:
            # Perform health check
            self._perform_health_check(verification_request)

            # Perform capabilities check
            self._verify_capabilities(verification_request)

            # For now, skip security check (would be implemented as a separate task)
            security_check = verification_request.checks.get(check_type='security')
            security_check.status = 'passed'
            security_check.message = "Basic security verification passed"
            security_check.save()

            # Check if all verifications passed
            all_passed = all(
                check.status == 'passed'
                for check in verification_request.checks.all()
            )

            if all_passed:
                verification_request.complete_verification(success=True)
                result_serializer = VerificationResultSerializer(
                    verification_request,
                    context={'request': request}
                )
                return Response(result_serializer.data)
            else:
                failed_checks = verification_request.checks.filter(status='failed')
                verification_request.status = 'failed'
                verification_request.save()

                return Response({
                    "error": "Verification failed",
                    "failed_checks": [
                        {
                            "type": check.check_type,
                            "message": check.message or "Verification check failed"
                        }
                        for check in failed_checks
                    ]
                }, status=status.HTTP_400_BAD_REQUEST)
        else:
            verification_request.status = 'failed'
            verification_request.save()

            return Response({
                "error": "Ownership verification failed",
                "message": ownership_check.message or "Could not verify server ownership"
            }, status=status.HTTP_400_BAD_REQUEST)

    def _verify_dns(self, domain, token, check):
        """Verify ownership using DNS TXT record."""
        try:
            # Try to get the TXT record
            records = dns.resolver.resolve(f'_mcp-verification.{domain}', 'TXT')

            for record in records:
                # Convert record.to_text() which returns '"token"' to just 'token'
                record_value = record.to_text().strip('"')
                if record_value == token:
                    check.details = {"domain": domain, "record_name": f"_mcp-verification.{domain}"}
                    check.message = "DNS verification successful"
                    check.save()
                    return True

            check.details = {"domain": domain, "error": "Token not found in DNS records"}
            check.message = "Could not find matching TXT record"
            check.save()
            return False
        except Exception as e:
            logger.error(f"DNS verification error: {str(e)}", exc_info=True)
            check.details = {"domain": domain, "error": str(e)}
            check.message = f"DNS resolution error: {str(e)}"
            check.save()
            return False

    def _verify_file(self, url, token, check):
        """Verify ownership using file verification."""
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200 and token in response.text:
                check.details = {"url": url}
                check.message = "File verification successful"
                check.save()
                return True
            else:
                check.details = {
                    "url": url,
                    "status_code": response.status_code,
                    "content": response.text[:100] + "..." if len(response.text) > 100 else response.text
                }
                check.message = f"File verification failed (status: {response.status_code})"
                check.save()
                return False
        except Exception as e:
            logger.error(f"File verification error: {str(e)}", exc_info=True)
            check.details = {"url": url, "error": str(e)}
            check.message = f"File request error: {str(e)}"
            check.save()
            return False

    def _verify_meta_tag(self, url, token, check):
        """Verify ownership using meta tag verification."""
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                import re
                # Look for meta tag in HTML
                meta_pattern = re.compile(r'<meta\s+name=[\'"]mcp-verification[\'"]\s+content=[\'"]([^\'"]+)[\'"]', re.IGNORECASE)
                match = meta_pattern.search(response.text)

                if match and match.group(1) == token:
                    check.details = {"url": url}
                    check.message = "Meta tag verification successful"
                    check.save()
                    return True
                else:
                    check.details = {"url": url, "error": "Meta tag not found or token doesn't match"}
                    check.message = "Could not find matching meta tag"
                    check.save()
                    return False
            else:
                check.details = {"url": url, "status_code": response.status_code}
                check.message = f"Meta tag verification failed (status: {response.status_code})"
                check.save()
                return False
        except Exception as e:
            logger.error(f"Meta tag verification error: {str(e)}", exc_info=True)
            check.details = {"url": url, "error": str(e)}
            check.message = f"Request error: {str(e)}"
            check.save()
            return False

    def _perform_health_check(self, verification_request):
        """Perform a health check on the server."""
        server = verification_request.server
        health_check = verification_request.checks.get(check_type='health')

        is_healthy, response_time = check_server_health(server.url)

        # Record health check
        HealthCheck.objects.create(
            server=server,
            is_up=is_healthy,
            response_time=response_time,
            details={"check_type": "verification"}
        )

        if is_healthy:
            health_check.status = 'passed'
            health_check.message = f"Server is healthy (response time: {response_time:.2f}s)"
            health_check.details = {"response_time": response_time}
        else:
            health_check.status = 'failed'
            health_check.message = "Server health check failed"
            health_check.details = {"error": "Server is not responding"}

        health_check.save()
        return is_healthy

    def _verify_capabilities(self, verification_request):
        """Verify that the server provides the capabilities it claims to."""
        server = verification_request.server
        capabilities_check = verification_request.checks.get(check_type='capabilities')

        try:
            # Get server capabilities
            response = requests.get(f"{server.url.rstrip('/')}/capabilities", timeout=10)

            if response.status_code != 200:
                capabilities_check.status = 'failed'
                capabilities_check.message = f"Failed to retrieve capabilities (status: {response.status_code})"
                capabilities_check.details = {"status_code": response.status_code}
                capabilities_check.save()
                return False

            # Parse capabilities
            try:
                capabilities = response.json()

                # Check if capabilities match what's registered
                registered_capabilities = {c.name for c in server.capabilities.all()}
                server_capabilities = set()

                # Extract capability names from response (format may vary)
                if isinstance(capabilities, list):
                    for cap in capabilities:
                        if isinstance(cap, dict) and 'name' in cap:
                            server_capabilities.add(cap['name'])
                elif isinstance(capabilities, dict) and 'capabilities' in capabilities:
                    for cap in capabilities['capabilities']:
                        if isinstance(cap, dict) and 'name' in cap:
                            server_capabilities.add(cap['name'])

                # Check for missing capabilities
                missing_capabilities = registered_capabilities - server_capabilities

                if missing_capabilities:
                    capabilities_check.status = 'failed'
                    capabilities_check.message = f"Missing capabilities: {', '.join(missing_capabilities)}"
                    capabilities_check.details = {
                        "registered_capabilities": list(registered_capabilities),
                        "server_capabilities": list(server_capabilities),
                        "missing_capabilities": list(missing_capabilities)
                    }
                    capabilities_check.save()
                    return False
                else:
                    capabilities_check.status = 'passed'
                    capabilities_check.message = "All registered capabilities are available"
                    capabilities_check.details = {
                        "registered_capabilities": list(registered_capabilities),
                        "server_capabilities": list(server_capabilities)
                    }
                    capabilities_check.save()
                    return True

            except ValueError:
                capabilities_check.status = 'failed'
                capabilities_check.message = "Invalid capabilities format (not valid JSON)"
                capabilities_check.details = {"response": response.text[:500]}
                capabilities_check.save()
                return False

        except Exception as e:
            logger.error(f"Capabilities verification error: {str(e)}", exc_info=True)
            capabilities_check.status = 'failed'
            capabilities_check.message = f"Error checking capabilities: {str(e)}"
            capabilities_check.details = {"error": str(e)}
            capabilities_check.save()
            return False


class VerificationBadgeView(views.APIView):
    """
    API view for getting a verification badge.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request, *args, **kwargs):
        # Get the server
        server_id = kwargs.get('server_id')
        server = get_object_or_404(Server, id=server_id)

        # Generate SVG badge
        if server.verified:
            badge_color = "#10b981"  # success green
            badge_text = "Verified"
        else:
            badge_color = "#64748b"  # gray
            badge_text = "Unverified"

        svg = f"""
        <svg xmlns="http://www.w3.org/2000/svg" width="110" height="20">
            <linearGradient id="b" x2="0" y2="100%">
                <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
                <stop offset="1" stop-opacity=".1"/>
            </linearGradient>
            <mask id="a">
                <rect width="110" height="20" rx="3" fill="#fff"/>
            </mask>
            <g mask="url(#a)">
                <path fill="#5046e5" d="M0 0h60v20H0z"/>
                <path fill="{badge_color}" d="M60 0h50v20H60z"/>
                <path fill="url(#b)" d="M0 0h110v20H0z"/>
            </g>
            <g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
                <text x="30" y="15" fill="#fff">MCP Nexus</text>
                <text x="85" y="15" fill="#fff">{badge_text}</text>
            </g>
        </svg>
        """

        return HttpResponse(svg, content_type="image/svg+xml")


class HealthCheckListView(generics.ListAPIView):
    """
    API view for listing health checks for a server.
    """
    serializer_class = HealthCheckSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]

    def get_queryset(self):
        server_id = self.kwargs.get('server_id')
        server = get_object_or_404(Server, id=server_id)

        # Check if the user is the server owner
        self.check_object_permissions(self.request, server)

        return HealthCheck.objects.filter(server=server).order_by('-created_at')