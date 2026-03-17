from sqlalchemy import Column, Integer, String, Float
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class Car(Base):
    __tablename__ = 'cars'

    id = Column(Integer, primary_key=True)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    url = Column(String, nullable=False)

    def __repr__(self):
        return f"<Car(make='{self.make}', model='{self.model}', year='{self.year}', price='{self.price}', url='{self.url}')>"