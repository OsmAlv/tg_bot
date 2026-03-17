import requests
from bs4 import BeautifulSoup

def parse_kbchachacha(url):
    response = requests.get(url)
    if response.status_code != 200:
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    listings = []

    for item in soup.select('.car-listing'):
        title = item.select_one('.car-title').get_text(strip=True)
        price = item.select_one('.car-price').get_text(strip=True)
        details = item.select_one('.car-details').get_text(strip=True)

        listings.append({
            'title': title,
            'price': price,
            'details': details,
            'url': url
        })

    return listings