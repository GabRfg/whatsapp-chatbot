FROM python:3.12-slim

# Não escrever .pyc e não bufferizar a saída: logs aparecem na hora no
# docker logs, em vez de ficarem presos no buffer até o processo encerrar.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# As dependências vêm antes do código para que a camada de instalação só seja
# refeita quando requirements.txt mudar, e não a cada edição do Python.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY flows ./flows

# Um processo comprometido rodando como root tem o container inteiro. Este
# usuário não possui os arquivos da aplicação, então também não pode alterá-los.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R root:root /app \
    && chmod -R a-w /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
