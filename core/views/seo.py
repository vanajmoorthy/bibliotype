"""SEO views: robots.txt and sitemap.xml."""

import logging
import os

from django.conf import settings
from django.http import HttpResponse
from django.urls import reverse

logger = logging.getLogger(__name__)


def robots_txt_view(request):
    """Serve robots.txt file."""
    static_dirs = getattr(settings, "STATICFILES_DIRS", [])
    if static_dirs:
        robots_path = os.path.join(static_dirs[0], "robots.txt")
        try:
            with open(robots_path, "r") as f:
                content = f.read()
            # Replace sitemap URL with actual domain
            sitemap_url = f"{request.scheme}://{request.get_host()}/sitemap.xml"
            content = content.replace("https://bibliotype.com/sitemap.xml", sitemap_url)
            return HttpResponse(content, content_type="text/plain")
        except (FileNotFoundError, IndexError):
            pass
    # Fallback if file doesn't exist
    sitemap_url = f"{request.scheme}://{request.get_host()}/sitemap.xml"
    return HttpResponse(f"User-agent: *\nAllow: /\n\nSitemap: {sitemap_url}", content_type="text/plain")


def sitemap_xml_view(request):
    """Generate and serve sitemap.xml."""
    from django.utils import timezone

    from ..models import UserProfile

    base_url = f"{request.scheme}://{request.get_host()}"
    today = timezone.now().strftime("%Y-%m-%d")

    public_profiles = UserProfile.objects.filter(is_public=True, dna_data__isnull=False).select_related("user")[
        :1000
    ]  # Limit to 1000 most recent public profiles

    urls = [
        {
            "loc": f"{base_url}/",
            "lastmod": today,
            "changefreq": "daily",
            "priority": "1.0",
        },
        {
            "loc": f"{base_url}/login/",
            "lastmod": today,
            "changefreq": "monthly",
            "priority": "0.8",
        },
        {
            "loc": f"{base_url}/signup/",
            "lastmod": today,
            "changefreq": "monthly",
            "priority": "0.8",
        },
        {
            "loc": f"{base_url}/about/",
            "lastmod": today,
            "changefreq": "monthly",
            "priority": "0.6",
        },
        {
            "loc": f"{base_url}/privacy/",
            "lastmod": today,
            "changefreq": "monthly",
            "priority": "0.4",
        },
        {
            "loc": f"{base_url}/terms/",
            "lastmod": today,
            "changefreq": "monthly",
            "priority": "0.4",
        },
    ]

    # Add public profile URLs
    for profile in public_profiles:
        try:
            profile_url = reverse("core:public_profile", kwargs={"username": profile.user.username})
            lastmod = today
            if profile.recommendations_generated_at:
                lastmod = profile.recommendations_generated_at.strftime("%Y-%m-%d")
            urls.append(
                {
                    "loc": f"{base_url}{profile_url}",
                    "lastmod": lastmod,
                    "changefreq": "weekly",
                    "priority": "0.7",
                }
            )
        except Exception:
            logger.warning(f"Error generating sitemap entry for user {profile.user.username}", exc_info=True)
            continue

    sitemap_xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap_xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'

    for url_data in urls:
        sitemap_xml += "  <url>\n"
        sitemap_xml += f'    <loc>{url_data["loc"]}</loc>\n'
        sitemap_xml += f'    <lastmod>{url_data["lastmod"]}</lastmod>\n'
        sitemap_xml += f'    <changefreq>{url_data["changefreq"]}</changefreq>\n'
        sitemap_xml += f'    <priority>{url_data["priority"]}</priority>\n'
        sitemap_xml += "  </url>\n"

    sitemap_xml += "</urlset>"

    return HttpResponse(sitemap_xml, content_type="application/xml")
