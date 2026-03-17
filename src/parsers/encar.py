import requests
from bs4 import BeautifulSoup

def parse_encar_listing(url):
    response = requests.get(url)
    if response.status_code != 200:
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    car_data = {}

    # Example parsing logic (this will need to be adjusted based on actual HTML structure)
    car_data['title'] = soup.find('h1', class_='car-title').text.strip()
    car_data['price'] = soup.find('span', class_='car-price').text.strip()
    car_data['mileage'] = soup.find('span', class_='car-mileage').text.strip()
    car_data['year'] = soup.find('span', class_='car-year').text.strip()

    return car_data