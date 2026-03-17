class Car:
    def __init__(self, make, model, year, price, url, description):
        self.make = make
        self.model = model
        self.year = year
        self.price = price
        self.url = url
        self.description = description

    def __repr__(self):
        return f"{self.year} {self.make} {self.model} - {self.price} - {self.url}"

    def to_dict(self):
        return {
            "make": self.make,
            "model": self.model,
            "year": self.year,
            "price": self.price,
            "url": self.url,
            "description": self.description,
        }