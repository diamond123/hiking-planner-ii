import logging
import requests

from langchain_tavily import TavilySearch

logger = logging.getLogger(__name__)
_tavily = TavilySearch(max_results=3)


def _format_results(raw: dict) -> str:
    results = raw.get("results", [])
    if not results:
        return "No search results found."
    lines = []
    for r in results:
        lines.append(f"- {r.get('title', '')}: {r.get('content', '')}")
    return "\n".join(lines)

def _get_nws_forecast(lat, lon, target_date):
    # Identifies your application to the NWS servers
    headers = {
        "User-Agent": "MyOutdoorActivityApp/1.0 (contact@example.com)"
    }
    
    # STEP 1: Fetch the grid point metadata for your coordinates
    points_url = f"https://api.weather.gov/points/{lat},{lon}"
    # print(f"Fetching location metadata from: {points_url}")
    
    response = requests.get(points_url, headers=headers)
    if response.status_code != 200:
        logger.error(f"Error locating grid points. Status code: {response.status_code}")
        return None
        
    metadata = response.json()
    
    # Extract the exact forecast URL for this specific geographic grid
    forecast_url = metadata["properties"]["forecast"]
    # print(f"Grid found! Fetching actual forecast from: {forecast_url}\n")
    
    # STEP 2: Fetch the actual weather forecast payload
    forecast_response = requests.get(forecast_url, headers=headers)
    if forecast_response.status_code != 200:
        logger.error(f"Error fetching forecast. Status code: {forecast_response.status_code}")
        return None
        
    forecast_data = forecast_response.json()
    periods = forecast_data["properties"]["periods"]
    
    # print(f"--- Weather Forecast for {target_date} ---")
    
    # Loop and look for matching start dates
    for period in periods:
        # Extract just the 'YYYY-MM-DD' part of the NWS startTime string
        period_date = period["startTime"].split("T")[0]
        
        if period_date == target_date:
            # print(f"Period Name: {period['name']}")
            # print(f"Temperature: {period['temperature']}°{period['temperatureUnit']}")
            # print(f"Condition: {period['shortForecast']}")
            # print(f"Details: {period['detailedForecast']}")
            return {"temperature": f"{period['temperature']}°{period['temperatureUnit']}", "shortForecast": period['shortForecast']}
    return None

def search_weather(location_latlon: dict | None, hiking_date: str) -> str:
    # location_part = f" near {location_text}" if location_text else ""
    # query = f"weather forecast for {title}{location_part} on {hiking_date}"
    # raw = _tavily.invoke({"query": query})
    # return _format_results(raw)
    weather_info = _get_nws_forecast(
        lat=location_latlon["lat"],
        lon=location_latlon["lon"],
        target_date=hiking_date,
    )
    return str(weather_info) if weather_info else "No weather information available."


def search_trail_conditions(title: str, location_text: str | None, hiking_date: str) -> str:
    location_part = f" near {location_text}" if location_text else ""
    query = f"{title}{location_part} trail conditions closures maintenance on {hiking_date}"
    raw = _tavily.invoke({"query": query})
    return _format_results(raw)
