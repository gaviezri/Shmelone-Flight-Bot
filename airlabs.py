import logging
import requests

BASE_URL = "https://airlabs.co/api/v9"


def get_routes(api_key: str, airline_iata: str, departure_airports: list[str]) -> dict:
    """
    Fetch all routes for an airline departing from the given airports.
    Makes one API call per departure airport and merges results.
    Handles pagination automatically.
    """
    all_routes = {}
    for dep_iata in departure_airports:
        routes = _fetch_all_pages(api_key, airline_iata, dep_iata)
        all_routes.update({_route_key(r): r for r in routes})
    return all_routes


def _fetch_all_pages(api_key: str, airline_iata: str, dep_iata: str) -> list[dict]:
    results = []
    offset = 0
    limit = 50  # free tier max per call

    while True:
        params = {
            "airline_iata": airline_iata,
            "dep_iata": dep_iata,
            "limit": limit,
            "offset": offset,
            "api_key": api_key,
        }
        response = requests.get(f"{BASE_URL}/routes", params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            raise RuntimeError(f"Airlabs error: {data['error']}")

        page = data.get("response", [])
        results.extend(page)

        logging.info(
            "Fetched %s dep=%s offset=%d → %d routes (has_more=%s)",
            airline_iata, dep_iata, offset, len(page), data["request"].get("has_more"),
        )

        if not data["request"].get("has_more") or len(page) < limit:
            break
        offset += limit

    return results


def _route_key(route: dict) -> str:
    # Use flight_iata + dep_time as key: same flight number can have multiple
    # entries with different departure times (e.g. different days/seasons).
    flight = route.get("flight_iata") or f"{route.get('airline_iata', '')}{route.get('flight_number', '')}"
    dep_time = route.get("dep_time", "")
    days = "_".join(sorted(route.get("days") or []))
    return f"{flight}_{dep_time}_{days}"