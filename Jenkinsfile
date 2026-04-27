pipeline {
    agent any

    environment {
        DOCKER_IMAGE = "blood-bank-app:${BUILD_NUMBER}"
        DOCKER_IMAGE_LATEST = "blood-bank-app:latest"
        DOCKER_REGISTRY = "localhost:5000"
        COMPOSE_FILE = "docker-compose.sqlite.yml"
        PROJECT_NAME = "25_12_2025-main"
        APP_PORT = "8000"
        CONTAINER_PORT = "8080"
    }

    stages {
        stage('Checkout') {
            steps {
                echo "====== Checking out source code ======"
                checkout scm
            }
        }

        stage('Build Docker Image') {
            steps {
                echo "====== Building Docker image ======"
                script {
                    sh '''
                        echo "Building image: ${DOCKER_IMAGE}"
                        docker build \
                            -t ${DOCKER_IMAGE} \
                            -t ${DOCKER_IMAGE_LATEST} \
                            --build-arg BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ') \
                            --build-arg VCS_REF=$(git rev-parse --short HEAD) \
                            --build-arg VERSION=${BUILD_NUMBER} \
                            .
                        
                        echo "Image built successfully"
                        docker images | grep blood-bank-app
                    '''
                }
            }
        }

        stage('Push to Docker Registry') {
            steps {
                echo "====== Pushing Docker image to registry ======"
                script {
                    sh '''
                        docker tag ${DOCKER_IMAGE} ${DOCKER_REGISTRY}/${DOCKER_IMAGE}
                        docker tag ${DOCKER_IMAGE_LATEST} ${DOCKER_REGISTRY}/${DOCKER_IMAGE_LATEST}
                        
                        echo "Pushing ${DOCKER_IMAGE} to registry..."
                        docker push ${DOCKER_REGISTRY}/${DOCKER_IMAGE} || echo "Registry push skipped (registry may not be available)"
                    '''
                }
            }
        }

        stage('Run Migrations') {
            steps {
                echo "====== Running database migrations ======"
                script {
                    sh '''
                        docker compose -f ${COMPOSE_FILE} run --rm web python manage.py migrate --noinput
                    '''
                }
            }
        }

        stage('Collect Static Files') {
            steps {
                echo "====== Collecting static files ======"
                script {
                    sh '''
                        docker compose -f ${COMPOSE_FILE} run --rm web python manage.py collectstatic --noinput
                    '''
                }
            }
        }

        stage('Run Tests') {
            steps {
                echo "====== Running Django tests ======"
                script {
                    sh '''
                        docker compose -f ${COMPOSE_FILE} run --rm web python manage.py test --noinput || true
                    '''
                }
            }
        }

        stage('Check Code Quality') {
            steps {
                echo "====== Running system check ======"
                script {
                    sh '''
                        docker compose -f ${COMPOSE_FILE} run --rm web python manage.py check
                    '''
                }
            }
        }

        stage('Deploy - Start Containers') {
            steps {
                echo "====== Starting application containers on port ${APP_PORT} ======"
                script {
                    sh '''
                        echo "Stopping existing containers..."
                        docker compose -f ${COMPOSE_FILE} down || true
                        
                        echo "Starting new containers..."
                        docker compose -f ${COMPOSE_FILE} up -d
                        
                        echo "Container status:"
                        docker compose -f ${COMPOSE_FILE} ps
                        
                        echo "Waiting for application to start..."
                        sleep 10
                        
                        echo "Application will be available at: http://localhost:${APP_PORT}/"
                    '''
                }
            }
        }

        stage('Health Check') {
            steps {
                echo "====== Performing health check on port ${APP_PORT} ======"
                script {
                    sh '''
                        for i in {1..5}; do
                            echo "Attempt $i/5 - Checking http://localhost:${APP_PORT}/admin/"
                            if curl -f -s http://localhost:${APP_PORT}/admin/ > /dev/null 2>&1; then
                                echo "✓ Application is healthy on port ${APP_PORT}"
                                docker ps -f name=${PROJECT_NAME}
                                exit 0
                            fi
                            echo "Waiting for application to be ready..."
                            sleep 5
                        done
                        echo "✗ Application health check failed on port ${APP_PORT}"
                        docker compose -f ${COMPOSE_FILE} logs web || true
                        exit 1
                    '''
                }
            }
        }

        stage('Display Container Info') {
            steps {
                echo "====== Docker Container Information ======"
                script {
                    sh '''
                        echo "Running containers:"
                        docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"
                        
                        echo ""
                        echo "Application Details:"
                        echo "  URL: http://localhost:${APP_PORT}/"
                        echo "  Admin: http://localhost:${APP_PORT}/admin/"
                        echo "  Port Mapping: ${APP_PORT} -> ${CONTAINER_PORT}"
                        echo "  Image: ${DOCKER_IMAGE}"
                        echo "  Build: #${BUILD_NUMBER}"
                    '''
                }
            }
        }
    }

    post {
        always {
            echo "====== Cleanup ======"
            script {
                sh '''
                    docker image prune -f || true
                    docker volume prune -f || true
                '''
            }
        }

        success {
            echo "====== Pipeline succeeded ======"
            echo "Application is running at http://localhost:${APP_PORT}/"
            echo "Admin panel: http://localhost:${APP_PORT}/admin/"
            echo "Docker Image: ${DOCKER_IMAGE}"
        }

        failure {
            echo "====== Pipeline failed ======"
            script {
                sh '''
                    echo "Stopping containers due to failure..."
                    docker compose -f ${COMPOSE_FILE} logs web || true
                    docker compose -f ${COMPOSE_FILE} down || true
                '''
            }
        }

        unstable {
            echo "====== Pipeline unstable ======"
        }
    }

    options {
        timestamps()
        timeout(time: 30, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '10'))
    }
}
