#include <Arduino.h>

const int analogInPin0 = A0;
const int analogInPin1 = A1;
const int analogInPin2 = A2;
const int analogInPin3 = A3;
const int analogInPin4 = A4;

void setup()
{
  Serial.begin(9600);
  pinMode(analogInPin0, INPUT);
  pinMode(analogInPin1, INPUT);
  pinMode(analogInPin2, INPUT);
  pinMode(analogInPin3, INPUT);
  pinMode(analogInPin4, INPUT);
}

void loop()
{
  int sensorValue0 = analogRead(analogInPin0);
  int sensorValue1 = analogRead(analogInPin1);
  int sensorValue2 = analogRead(analogInPin2);
  int sensorValue3 = analogRead(analogInPin3);
  int sensorValue4 = analogRead(analogInPin4);
  int mappedValue0 = map(sensorValue0, 0, 1023, 0, 100);
  int mappedValue1 = map(sensorValue1, 0, 1023, 0, 100);
  int mappedValue2 = map(sensorValue2, 0, 1023, 0, 100);
  int mappedValue3 = map(sensorValue3, 0, 1023, 0, 100);
  int mappedValue4 = map(sensorValue4, 0, 1023, 0, 100);
  if (mappedValue0 == 99) { mappedValue0 = 100; }
  if (mappedValue1 == 99) { mappedValue1 = 100; }
  if (mappedValue2 == 99) { mappedValue2 = 100; }
  if (mappedValue3 == 99) { mappedValue3 = 100; }
  if (mappedValue4 == 99) { mappedValue4 = 100; }

  Serial.print(String(mappedValue0) + ", ");
  Serial.print(String(mappedValue1) + ", ");
  Serial.print(String(mappedValue2) + ", ");
  Serial.print(String(mappedValue3) + ", ");
  Serial.print(String(mappedValue4) + ", ");
  Serial.println();

  delay(20);
}
