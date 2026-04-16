# base image
FROM python:3.13-slim

# /app 폴더에서 내가 만든 코드를 따로 관리하겠다.
WORKDIR /app

# main.py를 Docker 안에 /app으로 옮기겠다.
COPY . .

# 이미지를 만들 때 설치하는 것
RUN pip install -r requirements.txt

# 만들어진 이미지를 최종적인 컨테이너로 띄운다. 
# uvicorn --host를 바꿔줘야한다.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
