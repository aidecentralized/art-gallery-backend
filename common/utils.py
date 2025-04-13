import logging
import uuid
import requests
from django.utils import timezone
from django.conf import settings
from rest_framework.views import exception_handler
from rest_framework.exceptions import ValidationError, NotAuthenticated, PermissionDenied, NotFound
from rest_framework.response import Response

logger = logging.getLogger('mcp_nexus')

def generate_unique_id():
    """Generate a unique identifier for database records."""
    return str(uuid.uuid4())

def custom_exception_handler(exc, context):
    """
    Custom exception handler for DRF.
    
    Returns responses in the format:
    {
        "code": "error_code",
        "message": "Error message",
        "details": { additional error details }
    }
    """
    # Call REST framework's default exception handler first
    response = exception_handler(exc, context)
    
    # If response is None, this is an unhandled exception
    if response is None:
        logger.exception("Unhandled exception", exc_info=exc)
        return Response(
            {
                'code': 'server_error',
                'message': 'An unexpected error occurred',
                'details': {'error': str(exc)} if settings.DEBUG else {}
            },
            status=500
        )
    
    # Format the response
    if isinstance(exc, ValidationError):
        response.data = {
            'code': 'validation_error',
            'message': 'Validation failed',
            'details': exc.detail
        }
    elif isinstance(exc, NotAuthenticated):
        response.data = {
            'code': 'not_authenticated',
            'message': 'Authentication credentials were not provided',
            'details': {}
        }
    elif isinstance(exc, PermissionDenied):
        response.data = {
            'code': 'permission_denied',
            'message': 'You do not have permission to perform this action',
            'details': {}
        }
    elif isinstance(exc, NotFound):
        response.data = {
            'code': 'not_found',
            'message': 'The requested resource was not found',
            'details': {}
        }
    else:
        # Generic error handling
        response.data = {
            'code': 'error',
            'message': str(exc),
            'details': {}
        }
    
    return response

def validate_mcp_server_url(url):
    """
    Validate that a URL is actually pointing to an MCP server.
    
    Performs a basic check by requesting the server's capabilities.
    """
    try:
        # TODO: Check for MCP specific headers instead of just README.md
        response = requests.get(f"{url}", timeout=5)
        if response.status_code == 200:
            return True, {"message": "Server is reachable", "response_time": response.elapsed.total_seconds()}
        return False, {"error": "Website is not reachable or does not return a valid response"}
    except requests.RequestException as e:
        return False, {"error": str(e)}

def check_server_health(url):
    """
    Check if an MCP server is healthy and responding.
    """
    try:
        logger.info(f"Checking server health for URL: {url}")
        response = requests.get(f"{url}", timeout=3)
        logger.info(f"Health check response: status={response.status_code}, time={response.elapsed.total_seconds()}")
        return response.status_code == 200, response.elapsed.total_seconds()
    except requests.RequestException as e:
        logger.error(f"Health check failed with exception: {str(e)}")
        return False, 0

def extract_domain_from_url(url):
    """
    Extract the domain from a URL.
    """
    from urllib.parse import urlparse
    return urlparse(url).netloc

def get_client_ip(request):
    """
    Get the client IP address from a request.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

def timestamp_now():
    """
    Get the current timestamp in ISO format.
    """
    return timezone.now().isoformat()