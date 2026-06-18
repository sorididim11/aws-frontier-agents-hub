# Design Document: AWS DevOps Agent Test Environment

## Overview

AWS DevOps Agent 테스트 환경은 현실적인 DevOps 시나리오에서 발생할 수 있는 문제들을 체계적으로 시뮬레이션하고, AWS DevOps Agent의 자율적 인시던트 대응 능력을 검증하기 위한 종합적인 테스트 플랫폼입니다.

이 시스템은 다음과 같은 핵심 구성요소로 이루어집니다:
- **Multi-tier MSA 테스트 애플리케이션**: 실제 운영 환경과 유사한 마이크로서비스 아키텍처
- **문제 시뮬레이션 엔진**: MSA 특화 문제들을 포함한 현실적인 장애 상황 생성
- **다중 관찰성 도구 통합**: CloudWatch, Datadog, New Relic 등 실제 운영에서 사용되는 도구들
- **Agent Space 관리**: 애플리케이션별 격리된 조사 환경
- **자동화된 검증 프레임워크**: DevOps Agent의 대응 결과를 정량적으로 평가

## Architecture

### High-Level Architecture

```mermaid
graph TB
    subgraph "Test Control Plane"
        TC[Test Controller]
        PS[Problem Simulator]
        VF[Validation Framework]
    end
    
    subgraph "AWS DevOps Agent"
        AS[Agent Space]
        DA[DevOps Agent]
        WT[Web App & Topology]
    end
    
    subgraph "Test Applications"
        subgraph "MSA Application Stack"
            WEB[Web Frontend]
            API[API Gateway]
            MS1[User Service]
            MS2[Order Service] 
            MS3[Payment Service]
            MS4[Notification Service]
        end
        
        subgraph "Infrastructure"
            LB[Load Balancer]
            ECS[ECS Cluster]
            RDS[RDS Database]
            CACHE[ElastiCache]
        end
    end
    
    subgraph "Observability & Integration"
        CW[CloudWatch]
        DD[Datadog]
        NR[New Relic]
        GH[GitHub Actions]
        SL[Slack]
        SN[ServiceNow]
    end
    
    TC --> PS
    PS --> MSA Application Stack
    PS --> Infrastructure
    
    DA --> AS
    AS --> CW
    AS --> DD
    AS --> NR
    AS --> GH
    
    DA --> SL
    SN --> DA
    
    VF --> DA
    VF --> Observability & Integration
```

### Component Architecture

#### 1. Test Application Layer (MSA Stack)

**Frontend Tier:**
- React-based SPA hosted on S3/CloudFront
- API Gateway as entry point
- Authentication via Cognito

**Microservices Tier:**
- 4개의 독립적인 마이크로서비스 (User, Order, Payment, Notification)
- ECS Fargate에서 컨테이너로 실행
- Service Mesh (AWS App Mesh) 적용
- Circuit Breaker 패턴 구현 (Hystrix/Resilience4j)

**Data Tier:**
- RDS PostgreSQL (Multi-AZ)
- ElastiCache Redis (클러스터 모드)
- S3 (파일 저장소)

#### 2. Problem Simulation Engine

**Infrastructure Problem Generator:**
- CPU/Memory 스파이크 시뮬레이션
- 네트워크 지연/파티셔닝
- 디스크 공간 부족
- 로드 밸런서 장애

**MSA-Specific Problem Generator:**
- Service Mesh 장애 (Envoy Proxy 오류)
- Circuit Breaker 오작동
- Service Discovery 문제
- 분산 트레이싱 누락
- 서비스 간 의존성 체인 장애
- Connection Pool 고갈

**Application Problem Generator:**
- 메모리 누수 시뮬레이션
- 데이터베이스 연결 타임아웃
- API 응답 지연
- 배치 작업 실패

#### 3. Agent Space Management

**Agent Space Configuration:**
```yaml
AgentSpace:
  Name: "msa-test-environment"
  Scope: 
    - AWS Account: "123456789012"
    - Regions: ["us-east-1", "us-west-2"]
    - Resources: 
      - ECS Clusters
      - RDS Instances
      - API Gateway APIs
      - Lambda Functions
  
  IAM Roles:
    - CrossAccountRole: "arn:aws:iam::123456789012:role/DevOpsAgentRole"
    - Permissions:
      - CloudWatch: Read
      - ECS: Describe
      - RDS: Describe
      - API Gateway: Read
  
  Integrations:
    - CloudWatch: Native
    - Datadog: MCP Server
    - New Relic: MCP Server
    - GitHub: Actions Integration
    - Slack: Webhook
    - ServiceNow: Webhook
```

## Components and Interfaces

### 1. Test Controller Service

**Responsibilities:**
- 테스트 시나리오 오케스트레이션
- 문제 시뮬레이션 트리거
- Agent 성능 메트릭 수집

**Interfaces:**
```typescript
interface TestController {
  // 테스트 시나리오 관리
  createScenario(scenario: TestScenario): Promise<string>
  executeScenario(scenarioId: string): Promise<ExecutionResult>
  stopScenario(scenarioId: string): Promise<void>
  
  // Agent Space 관리
  createAgentSpace(config: AgentSpaceConfig): Promise<string>
  configureIntegrations(agentSpaceId: string, integrations: Integration[]): Promise<void>
  
  // 검증 및 리포팅
  validateAgentResponse(incidentId: string): Promise<ValidationResult>
  generateReport(executionId: string): Promise<TestReport>
}
```

### 2. Problem Simulator

**MSA Problem Patterns:**
```typescript
interface MSAProblemSimulator {
  // Service Mesh 문제
  simulateEnvoyProxyFailure(serviceId: string): Promise<void>
  simulateServiceMeshPartition(services: string[]): Promise<void>
  
  // Circuit Breaker 문제
  forceCircuitBreakerOpen(serviceId: string): Promise<void>
  simulateCircuitBreakerFlapping(serviceId: string): Promise<void>
  
  // 서비스 의존성 문제
  simulateServiceDependencyFailure(dependencyChain: string[]): Promise<void>
  simulateCascadingFailure(rootService: string): Promise<void>
  
  // 분산 트레이싱 문제
  disableTracingForService(serviceId: string): Promise<void>
  simulateTracingDataLoss(percentage: number): Promise<void>
}
```

### 3. Observability Integration Layer

**Multi-Tool Data Correlation:**
```typescript
interface ObservabilityIntegration {
  // CloudWatch 통합
  cloudWatch: {
    metrics: CloudWatchMetrics
    logs: CloudWatchLogs
    alarms: CloudWatchAlarms
  }
  
  // Datadog MCP Server
  datadog: {
    mcpServer: DatadogMCPServer
    authentication: OAuth2Config
    metrics: DatadogMetrics
    traces: DatadogTraces
  }
  
  // New Relic MCP Server
  newRelic: {
    mcpServer: NewRelicMCPServer
    authentication: APIKeyConfig
    apm: NewRelicAPM
    infrastructure: NewRelicInfra
  }
  
  // GitHub Actions 통합
  github: {
    deploymentTracking: GitHubDeployments
    webhooks: GitHubWebhooks
  }
}
```

### 4. Slack Integration

**Real-time Collaboration:**
```typescript
interface SlackIntegration {
  // 인시던트 채널 관리
  createIncidentChannel(incidentId: string): Promise<string>
  inviteTeamMembers(channelId: string, members: string[]): Promise<void>
  
  // 실시간 업데이트
  postInvestigationUpdate(channelId: string, update: InvestigationUpdate): Promise<void>
  postMitigationPlan(channelId: string, plan: MitigationPlan): Promise<void>
  
  // 상호작용
  handleUserQuestion(channelId: string, question: string): Promise<void>
  createSupportCase(channelId: string, findings: AgentFindings): Promise<string>
}
```

## Data Models

### Test Scenario Model

```typescript
interface TestScenario {
  id: string
  name: string
  description: string
  
  // 대상 애플리케이션
  targetApplication: {
    agentSpaceId: string
    services: string[]
    infrastructure: string[]
  }
  
  // 시뮬레이션할 문제들
  problems: ProblemDefinition[]
  
  // 예상 결과
  expectedOutcomes: {
    detectionTimeSeconds: number
    rootCauseAccuracy: number
    mitigationRelevance: number
  }
  
  // 실행 설정
  execution: {
    duration: number
    autoTrigger: boolean
    webhookConfig?: WebhookConfig
  }
}

interface ProblemDefinition {
  type: 'infrastructure' | 'msa' | 'application' | 'security'
  category: string // 'circuit-breaker', 'memory-leak', 'network-partition' 등
  severity: 'low' | 'medium' | 'high' | 'critical'
  parameters: Record<string, any>
  timing: {
    startDelay: number
    duration: number
  }
}
```

### Agent Performance Model

```typescript
interface AgentPerformanceMetrics {
  incidentId: string
  agentSpaceId: string
  
  // 성능 메트릭
  performance: {
    detectionTime: number // 문제 감지까지 시간 (초)
    investigationTime: number // 조사 완료까지 시간 (초)
    resolutionTime: number // 해결책 제시까지 시간 (초)
  }
  
  // 정확도 메트릭
  accuracy: {
    rootCauseCorrect: boolean
    rootCauseConfidence: number // 0-1
    mitigationRelevance: number // 0-1
    falsePositives: number
  }
  
  // 통합 도구 활용
  toolUsage: {
    cloudWatchQueries: number
    datadogQueries: number
    newRelicQueries: number
    githubChecks: number
    slackInteractions: number
  }
  
  // 토폴로지 분석
  topology: {
    componentsAnalyzed: number
    relationshipsIdentified: number
    deploymentHistoryChecked: boolean
  }
}
```

### Validation Result Model

```typescript
interface ValidationResult {
  testExecutionId: string
  scenarioId: string
  
  // 전체 결과
  overallScore: number // 0-100
  passed: boolean
  
  // 세부 검증 결과
  detection: {
    detected: boolean
    timeToDetection: number
    expectedTime: number
    score: number
  }
  
  rootCauseAnalysis: {
    correctRootCause: boolean
    confidence: number
    relevantFactorsIdentified: string[]
    missedFactors: string[]
    score: number
  }
  
  mitigation: {
    relevantSuggestions: number
    implementableSuggestions: number
    timeToSuggestion: number
    score: number
  }
  
  collaboration: {
    slackUpdatesProvided: boolean
    teamNotificationsSent: boolean
    supportCaseCreated: boolean
    score: number
  }
}
```

## Error Handling

### Problem Simulation Failures

**Circuit Breaker for Simulators:**
- 각 문제 시뮬레이터에 Circuit Breaker 패턴 적용
- 연속 실패 시 자동 복구 메커니즘
- 시뮬레이션 상태 모니터링 및 알림

**Rollback Mechanisms:**
- 모든 시뮬레이션에 대한 자동 롤백 기능
- 테스트 환경 상태 스냅샷 및 복원
- 긴급 정지 기능 (Kill Switch)

### Agent Integration Failures

**MCP Server Connection Issues:**
- 연결 재시도 로직 (Exponential Backoff)
- 대체 데이터 소스 활용
- 부분적 데이터로도 검증 진행

**Webhook Delivery Failures:**
- 웹훅 전송 재시도 메커니즘
- Dead Letter Queue 활용
- 수동 트리거 대안 제공

### Data Consistency Issues

**Multi-Tool Data Synchronization:**
- 타임스탬프 기반 데이터 정렬
- 데이터 소스별 지연 시간 보정
- 불일치 데이터 감지 및 알림

## Testing Strategy

### Property-Based Testing

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Correctness Properties

Based on the prework analysis, the following properties have been identified to validate the system's correctness:

**Property 1: Multi-Tier Architecture Deployment**
*For any* deployment configuration, the Test Environment should successfully create all required tiers (web, application, database) with proper AWS resources and configurations
**Validates: Requirements 1.2, 1.4, 1.5**

**Property 2: Comprehensive Problem Generation**
*For any* problem type specification (infrastructure, MSA, application, security, deployment), the Problem Simulator should be able to generate realistic scenarios of that type on demand
**Validates: Requirements 2.1, 2.2, 2.4, 2.6, 2.7**

**Property 3: MSA-Specific Problem Simulation**
*For any* microservices architecture deployment, the Problem Simulator should be able to create SPOF scenarios and service dependency chain failures that accurately reflect real MSA issues
**Validates: Requirements 2.3, 2.5**

**Property 4: Multi-Tool Observability Integration**
*For any* error or incident generated in the system, all configured observability tools (CloudWatch, Datadog, New Relic, etc.) should capture and display relevant telemetry data
**Validates: Requirements 3.1, 3.2, 3.5, 3.6**

**Property 5: Real-Time Monitoring and Alerting**
*For any* incident detection, the system should trigger appropriate alarms across all monitoring systems and provide real-time dashboard updates
**Validates: Requirements 3.3, 3.4**

**Property 6: Agent Space Isolation and Management**
*For any* Agent Space configuration, the system should create isolated environments with proper IAM roles and cross-account permissions without interference between spaces
**Validates: Requirements 4.1, 4.2**

**Property 7: Automated Investigation Triggering**
*For any* webhook-triggered incident from ServiceNow or PagerDuty, the DevOps Agent should automatically initiate investigations without manual intervention
**Validates: Requirements 4.3, 4.4**

**Property 8: Topology Mapping and Investigation Validation**
*For any* completed investigation, the system should have built accurate topology maps and the Validation Framework should be able to assess the accuracy of root cause identification
**Validates: Requirements 4.5, 4.6, 4.7**

**Property 9: Slack Integration and Real-Time Collaboration**
*For any* investigation, dedicated Slack channels should be created with real-time updates, team notifications, and interactive capabilities
**Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 10.6**

**Property 10: MCP Server Integration and Custom Tool Support**
*For any* custom tool or open-source observability solution integrated via MCP servers, the DevOps Agent should be able to securely access and correlate data during investigations
**Validates: Requirements 11.1, 11.2, 11.3, 11.4, 11.5, 11.6**

### Unit Testing Strategy

**Core Component Testing:**
- Test Controller Service: API endpoints, scenario orchestration, error handling
- Problem Simulator: Individual problem generation methods, rollback mechanisms
- Agent Space Manager: IAM role creation, resource isolation, configuration validation
- Observability Integration: Connection establishment, data retrieval, authentication

**Integration Testing:**
- End-to-end scenario execution
- Multi-tool data correlation
- Webhook delivery and processing
- Slack notification workflows

**Performance Testing:**
- Agent response time under various load conditions
- Concurrent scenario execution
- Resource utilization during problem simulation
- Data ingestion rates from multiple observability tools

## SDLC-Integrated DevOps Agent Test Strategy

### DevOps Agent Core Components Integration

**1. Resource Discovery & Topology Building**
DevOps Agent는 다음 순서로 작동합니다:
1. **Resource Discovery**: CloudFormation 스택을 통해 AWS 리소스 자동 발견
2. **Topology Mapping**: 리소스 간 관계 및 의존성 매핑
3. **Deployment History**: GitHub Actions와 CloudFormation 배포 이력 추적
4. **Telemetry Integration**: CloudWatch 메트릭/로그와 X-Ray 트레이스 연결

### Multi-Project GitHub Repository Structure

**GitHub Organization Setup:**
```
github.com/devops-agent-test-org/
├── infrastructure-as-code/           # 인프라 정의
│   ├── cloudformation/
│   │   ├── vpc-foundation.yml        # 기본 네트워킹
│   │   ├── ecs-platform.yml          # ECS 클러스터 + 서비스
│   │   ├── data-layer.yml            # RDS + ElastiCache
│   │   ├── api-gateway.yml           # API Gateway + Lambda
│   │   └── monitoring-stack.yml      # CloudWatch + X-Ray
│   └── parameters/
│       ├── dev.json
│       ├── test.json
│       └── prod.json
├── microservice-user-api/            # 개별 마이크로서비스
│   ├── src/
│   ├── Dockerfile
│   ├── .github/workflows/deploy.yml
│   └── cloudformation/service.yml
├── microservice-order-api/
├── microservice-payment-api/
├── microservice-notification/
├── frontend-web-app/                 # 프론트엔드
│   ├── src/
│   ├── build/
│   └── .github/workflows/deploy-s3.yml
└── devops-agent-test-controller/     # 테스트 컨트롤러
    ├── src/
    ├── scenarios/
    └── .github/workflows/run-tests.yml
```

### CloudFormation-Based Resource Discovery

**Infrastructure Stack Design:**
```yaml
# vpc-foundation.yml - 기본 인프라
AWSTemplateFormatVersion: '2010-09-09'
Description: 'DevOps Agent Test - Foundation Infrastructure'

Parameters:
  Environment:
    Type: String
    Default: 'devops-agent-test'
  
Resources:
  # VPC with proper tagging for Agent discovery
  TestVPC:
    Type: AWS::EC2::VPC
    Properties:
      CidrBlock: 10.0.0.0/16
      EnableDnsHostnames: true
      EnableDnsSupport: true
      Tags:
        - Key: Name
          Value: !Sub '${Environment}-vpc'
        - Key: DevOpsAgentTest
          Value: 'true'
        - Key: Component
          Value: 'networking'
        - Key: Tier
          Value: 'foundation'

  # Subnets for multi-AZ deployment
  PublicSubnet1:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref TestVPC
      CidrBlock: 10.0.1.0/24
      AvailabilityZone: !Select [0, !GetAZs '']
      MapPublicIpOnLaunch: true
      Tags:
        - Key: Name
          Value: !Sub '${Environment}-public-1'
        - Key: DevOpsAgentTest
          Value: 'true'
        - Key: Component
          Value: 'networking'
        - Key: SubnetType
          Value: 'public'

  # Application Load Balancer for service discovery
  ApplicationLoadBalancer:
    Type: AWS::ElasticLoadBalancingV2::LoadBalancer
    Properties:
      Name: !Sub '${Environment}-alb'
      Scheme: internet-facing
      Type: application
      Subnets:
        - !Ref PublicSubnet1
        - !Ref PublicSubnet2
      SecurityGroups:
        - !Ref ALBSecurityGroup
      Tags:
        - Key: DevOpsAgentTest
          Value: 'true'
        - Key: Component
          Value: 'load-balancer'
        - Key: Tier
          Value: 'presentation'

Outputs:
  VPCId:
    Description: VPC ID for other stacks
    Value: !Ref TestVPC
    Export:
      Name: !Sub '${Environment}-vpc-id'
  
  ALBArn:
    Description: Application Load Balancer ARN
    Value: !Ref ApplicationLoadBalancer
    Export:
      Name: !Sub '${Environment}-alb-arn'
```

**ECS Service Stack with Topology Metadata:**
```yaml
# ecs-platform.yml - 마이크로서비스 플랫폼
AWSTemplateFormatVersion: '2010-09-09'
Description: 'DevOps Agent Test - ECS Platform'

Parameters:
  Environment:
    Type: String
    Default: 'devops-agent-test'
  VPCId:
    Type: String
    Description: VPC ID from foundation stack

Resources:
  # ECS Cluster with service discovery
  ECSCluster:
    Type: AWS::ECS::Cluster
    Properties:
      ClusterName: !Sub '${Environment}-cluster'
      CapacityProviders:
        - FARGATE
        - FARGATE_SPOT
      DefaultCapacityProviderStrategy:
        - CapacityProvider: FARGATE
          Weight: 1
      ClusterSettings:
        - Name: containerInsights
          Value: enabled
      ServiceConnectDefaults:
        Namespace: !Sub '${Environment}-services'
      Tags:
        - Key: DevOpsAgentTest
          Value: 'true'
        - Key: Component
          Value: 'compute-platform'
        - Key: Tier
          Value: 'application'

  # Service Discovery Namespace
  ServiceDiscoveryNamespace:
    Type: AWS::ServiceDiscovery::PrivateDnsNamespace
    Properties:
      Name: !Sub '${Environment}.local'
      Vpc: !Ref VPCId
      Description: 'Service discovery for DevOps Agent test'
      Properties:
        DnsProperties:
          SOA:
            TTL: 60

  # User Service Definition
  UserServiceTaskDefinition:
    Type: AWS::ECS::TaskDefinition
    Properties:
      Family: !Sub '${Environment}-user-service'
      NetworkMode: awsvpc
      RequiresCompatibilities:
        - FARGATE
      Cpu: 256
      Memory: 512
      ExecutionRoleArn: !Ref ECSExecutionRole
      TaskRoleArn: !Ref ECSTaskRole
      ContainerDefinitions:
        - Name: user-service
          Image: !Sub '${AWS::AccountId}.dkr.ecr.${AWS::Region}.amazonaws.com/user-service:latest'
          PortMappings:
            - ContainerPort: 3000
              Protocol: tcp
          LogConfiguration:
            LogDriver: awslogs
            Options:
              awslogs-group: !Ref UserServiceLogGroup
              awslogs-region: !Ref AWS::Region
              awslogs-stream-prefix: ecs
          Environment:
            - Name: SERVICE_NAME
              Value: user-service
            - Name: ENVIRONMENT
              Value: !Ref Environment
            - Name: AWS_XRAY_TRACING_NAME
              Value: user-service
            - Name: AWS_XRAY_CONTEXT_MISSING
              Value: LOG_ERROR
          # Health check for service discovery
          HealthCheck:
            Command:
              - CMD-SHELL
              - curl -f http://localhost:3000/health || exit 1
            Interval: 30
            Timeout: 5
            Retries: 3
      Tags:
        - Key: DevOpsAgentTest
          Value: 'true'
        - Key: Component
          Value: 'microservice'
        - Key: ServiceName
          Value: 'user-service'
        - Key: Tier
          Value: 'application'

  # ECS Service with Service Connect
  UserService:
    Type: AWS::ECS::Service
    Properties:
      ServiceName: user-service
      Cluster: !Ref ECSCluster
      TaskDefinition: !Ref UserServiceTaskDefinition
      DesiredCount: 2
      LaunchType: FARGATE
      NetworkConfiguration:
        AwsvpcConfiguration:
          SecurityGroups:
            - !Ref ServiceSecurityGroup
          Subnets:
            - !ImportValue 
                Fn::Sub: '${Environment}-private-subnet-1'
            - !ImportValue 
                Fn::Sub: '${Environment}-private-subnet-2'
      # Service Connect for service mesh
      ServiceConnectConfiguration:
        Enabled: true
        Namespace: !GetAtt ServiceDiscoveryNamespace.Arn
        Services:
          - PortName: user-service-port
            DiscoveryName: user-service
            ClientAliases:
              - Port: 3000
                DnsName: user-service
      # Load balancer integration
      LoadBalancers:
        - ContainerName: user-service
          ContainerPort: 3000
          TargetGroupArn: !Ref UserServiceTargetGroup
      # Service discovery registration
      ServiceRegistries:
        - RegistryArn: !GetAtt UserServiceDiscovery.Arn
      Tags:
        - Key: DevOpsAgentTest
          Value: 'true'
        - Key: Component
          Value: 'microservice'
        - Key: ServiceName
          Value: 'user-service'
        - Key: Dependencies
          Value: 'database,cache'  # For topology mapping

  # CloudWatch Log Groups with structured logging
  UserServiceLogGroup:
    Type: AWS::Logs::LogGroup
    Properties:
      LogGroupName: !Sub '/aws/ecs/${Environment}/user-service'
      RetentionInDays: 7
      Tags:
        - Key: DevOpsAgentTest
          Value: 'true'
        - Key: Component
          Value: 'logging'
        - Key: ServiceName
          Value: 'user-service'
```

### GitHub Actions Integration for Deployment Tracking

**Service Deployment Workflow:**
```yaml
# .github/workflows/deploy-user-service.yml
name: Deploy User Service

on:
  push:
    branches: [main]
    paths: ['microservice-user-api/**']
  workflow_dispatch:

env:
  AWS_REGION: us-east-1
  ECR_REPOSITORY: user-service
  ECS_SERVICE: user-service
  ECS_CLUSTER: devops-agent-test-cluster

jobs:
  deploy:
    runs-on: ubuntu-latest
    
    steps:
    - name: Checkout code
      uses: actions/checkout@v3
    
    - name: Configure AWS credentials
      uses: aws-actions/configure-aws-credentials@v2
      with:
        aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
        aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
        aws-region: ${{ env.AWS_REGION }}
    
    - name: Login to Amazon ECR
      id: login-ecr
      uses: aws-actions/amazon-ecr-login@v1
    
    - name: Build and push Docker image
      id: build-image
      env:
        ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
        IMAGE_TAG: ${{ github.sha }}
      run: |
        cd microservice-user-api
        docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG .
        docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
        echo "image=$ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG" >> $GITHUB_OUTPUT
    
    # DevOps Agent가 추적할 수 있도록 배포 메타데이터 생성
    - name: Create deployment metadata
      env:
        IMAGE_URI: ${{ steps.build-image.outputs.image }}
      run: |
        # CloudFormation 스택 업데이트로 배포 추적
        aws cloudformation update-stack \
          --stack-name devops-agent-test-user-service \
          --template-body file://cloudformation/user-service.yml \
          --parameters ParameterKey=ImageURI,ParameterValue=$IMAGE_URI \
          --capabilities CAPABILITY_IAM \
          --tags Key=GitHubSHA,Value=${{ github.sha }} \
                 Key=DeploymentTime,Value=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
                 Key=Branch,Value=${{ github.ref_name }} \
                 Key=DevOpsAgentTest,Value=true
        
        # ECS 서비스 업데이트
        aws ecs update-service \
          --cluster $ECS_CLUSTER \
          --service $ECS_SERVICE \
          --force-new-deployment
    
    # DevOps Agent가 배포 이벤트를 추적할 수 있도록 EventBridge 이벤트 발송
    - name: Send deployment event
      run: |
        aws events put-events \
          --entries '[{
            "Source": "github.actions",
            "DetailType": "Service Deployment",
            "Detail": "{
              \"service\": \"user-service\",
              \"version\": \"${{ github.sha }}\",
              \"environment\": \"devops-agent-test\",
              \"deploymentTime\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
              \"repository\": \"${{ github.repository }}\",
              \"branch\": \"${{ github.ref_name }}\",
              \"imageUri\": \"${{ steps.build-image.outputs.image }}\"
            }"
          }]'
    
    - name: Wait for deployment completion
      run: |
        aws ecs wait services-stable \
          --cluster $ECS_CLUSTER \
          --services $ECS_SERVICE
    
    - name: Verify deployment health
      run: |
        # Health check via load balancer
        ALB_DNS=$(aws elbv2 describe-load-balancers \
          --names devops-agent-test-alb \
          --query 'LoadBalancers[0].DNSName' \
          --output text)
        
        # Wait for health check to pass
        for i in {1..30}; do
          if curl -f "http://$ALB_DNS/user-service/health"; then
            echo "Service is healthy"
            break
          fi
          echo "Waiting for service to be healthy... ($i/30)"
          sleep 10
        done
```

### CloudWatch Telemetry Integration

**Structured Logging and Metrics:**
```javascript
// microservice-user-api/src/monitoring.js
const AWS = require('aws-sdk');
const AWSXRay = require('aws-xray-sdk-core');

// X-Ray 트레이싱 설정
AWSXRay.captureAWS(AWS);
AWSXRay.captureHTTPsGlobal(require('http'));
AWSXRay.captureHTTPsGlobal(require('https'));

class ServiceMonitoring {
  constructor(serviceName) {
    this.serviceName = serviceName;
    this.cloudWatch = new AWS.CloudWatch();
  }

  // DevOps Agent가 분석할 수 있는 구조화된 로그
  logStructured(level, message, metadata = {}) {
    const logEntry = {
      timestamp: new Date().toISOString(),
      level: level,
      service: this.serviceName,
      message: message,
      traceId: AWSXRay.getTraceId(),
      segmentId: AWSXRay.getSegment()?.id,
      ...metadata
    };
    
    console.log(JSON.stringify(logEntry));
  }

  // 커스텀 메트릭 전송 (DevOps Agent 분석용)
  async sendMetric(metricName, value, unit = 'Count', dimensions = {}) {
    const params = {
      Namespace: 'DevOpsAgentTest/Application',
      MetricData: [{
        MetricName: metricName,
        Value: value,
        Unit: unit,
        Dimensions: [
          { Name: 'Service', Value: this.serviceName },
          ...Object.entries(dimensions).map(([key, value]) => ({
            Name: key,
            Value: value
          }))
        ],
        Timestamp: new Date()
      }]
    };

    try {
      await this.cloudWatch.putMetricData(params).promise();
    } catch (error) {
      this.logStructured('error', 'Failed to send metric', { 
        metricName, 
        error: error.message 
      });
    }
  }

  // 비즈니스 메트릭 (DevOps Agent가 상관관계 분석할 수 있도록)
  async recordBusinessMetric(operation, success, duration, metadata = {}) {
    // 성공/실패 메트릭
    await this.sendMetric(`${operation}.Success`, success ? 1 : 0, 'Count');
    
    // 응답 시간 메트릭
    await this.sendMetric(`${operation}.Duration`, duration, 'Milliseconds');
    
    // 구조화된 로그로 상세 정보
    this.logStructured('info', `Operation completed: ${operation}`, {
      operation,
      success,
      duration,
      ...metadata
    });
  }
}

module.exports = ServiceMonitoring;
```

### DevOps Agent Test Scenarios

**Topology-Aware Problem Simulation:**
```python
# devops-agent-test-controller/src/topology_aware_simulator.py
import boto3
import json
from datetime import datetime

class TopologyAwareProblemSimulator:
    def __init__(self):
        self.ecs_client = boto3.client('ecs')
        self.cloudformation = boto3.client('cloudformation')
        self.events_client = boto3.client('events')
        
    def get_service_topology(self):
        """CloudFormation 스택에서 서비스 토폴로지 추출"""
        
        stacks = self.cloudformation.list_stacks(
            StackStatusFilter=['CREATE_COMPLETE', 'UPDATE_COMPLETE']
        )
        
        topology = {
            'services': [],
            'dependencies': [],
            'infrastructure': []
        }
        
        for stack in stacks['StackSummaries']:
            if 'devops-agent-test' in stack['StackName']:
                resources = self.cloudformation.list_stack_resources(
                    StackName=stack['StackName']
                )
                
                for resource in resources['StackResourceSummaries']:
                    if resource['ResourceType'] == 'AWS::ECS::Service':
                        topology['services'].append({
                            'name': resource['LogicalResourceId'],
                            'arn': resource['PhysicalResourceId'],
                            'stack': stack['StackName']
                        })
        
        return topology
    
    def simulate_cascading_failure(self, root_service):
        """토폴로지 기반 연쇄 장애 시뮬레이션"""
        
        topology = self.get_service_topology()
        
        # 1. 루트 서비스 장애 발생
        self._cause_service_failure(root_service)
        
        # 2. 의존성 체인을 따라 장애 전파
        dependent_services = self._get_dependent_services(root_service, topology)
        
        for service in dependent_services:
            # 지연을 두고 의존 서비스들도 장애 발생
            time.sleep(30)  # 30초 후 연쇄 장애
            self._cause_service_degradation(service)
        
        # 3. DevOps Agent가 분석할 수 있도록 이벤트 발송
        self._send_incident_event({
            'incident_type': 'cascading_failure',
            'root_cause_service': root_service,
            'affected_services': dependent_services,
            'timeline': self._generate_failure_timeline(root_service, dependent_services)
        })
    
    def _cause_service_failure(self, service_name):
        """ECS 서비스 완전 장애"""
        
        # 서비스의 desired count를 0으로 설정
        self.ecs_client.update_service(
            cluster='devops-agent-test-cluster',
            service=service_name,
            desiredCount=0
        )
        
        # CloudWatch에 장애 메트릭 전송
        cloudwatch = boto3.client('cloudwatch')
        cloudwatch.put_metric_data(
            Namespace='DevOpsAgentTest/Simulation',
            MetricData=[{
                'MetricName': 'ServiceFailure',
                'Value': 1,
                'Unit': 'Count',
                'Dimensions': [
                    {'Name': 'Service', 'Value': service_name},
                    {'Name': 'FailureType', 'Value': 'complete_outage'}
                ]
            }]
        )
```

이렇게 SDLC 전체를 고려한 통합적인 접근으로 DevOps Agent가 실제로 어떻게 작동하는지 테스트할 수 있는 환경을 구성할 수 있습니다.

### Revised Agent Space Configuration

**AWS-Native Configuration:**
```yaml
# Agent Space Configuration (AWS-Only)
AgentSpace:
  Name: "msa-test-environment"
  Description: "Test environment using AWS native services"
  
  # AWS Account and Resource Scope
  AWSConfiguration:
    AccountId: "123456789012"
    AssumeRoleArn: "arn:aws:iam::123456789012:role/DevOpsAgentTestRole"
    Regions: ["us-east-1"]
    
    # Monitored AWS Resources
    Resources:
      - ResourceType: "AWS::ECS::Cluster"
        ResourceArn: "arn:aws:ecs:us-east-1:123456789012:cluster/msa-test-cluster"
      - ResourceType: "AWS::RDS::DBInstance" 
        ResourceArn: "arn:aws:rds:us-east-1:123456789012:db:msa-test-db"
      - ResourceType: "AWS::ApiGateway::RestApi"
        ResourceArn: "arn:aws:apigateway:us-east-1::/restapis/abc123def456"
      - ResourceType: "AWS::Lambda::Function"
        ResourceArn: "arn:aws:lambda:us-east-1:123456789012:function:*"

# Service Associations (AWS Native + Open Source)
ServiceAssociations:
  # CloudWatch (Native - Primary)
  - ServiceId: "cloudwatch"
    Configuration:
      Type: "AWS"
      Region: "us-east-1"
      LogGroups:
        - "/aws/ecs/user-service"
        - "/aws/ecs/order-service"
        - "/aws/ecs/payment-service"
        - "/aws/apigateway/msa-api"
        - "/aws/lambda/notification-service"
      MetricNamespaces:
        - "AWS/ECS"
        - "AWS/RDS"
        - "AWS/ApiGateway"
        - "AWS/Lambda"
      
  # X-Ray (Native Tracing)
  - ServiceId: "xray"
    Configuration:
      Type: "AWS"
      Region: "us-east-1"
      ServiceMap: true
      TraceAnalytics: true
  
  # GitHub (Free Public Repository)
  - ServiceId: "github"
    Configuration:
      Type: "GitHub"
      Repository: "your-org/msa-test-app"  # Public repository
      Authentication:
        Type: "PersonalAccessToken"
        Token: "${GITHUB_PAT}"  # Personal Access Token (free)
      Workflows:
        - ".github/workflows/deploy-services.yml"
  
  # Prometheus via MCP Server (Open Source)
  - ServiceId: "prometheus-mcp"
    Configuration:
      Type: "MCP"
      MCPServerEndpoint: "http://prometheus-mcp-server:8080"
      Authentication:
        Type: "None"  # Internal service
      DataSources:
        - Metrics: "container_*,http_*,application_*"
  
  # Grafana via MCP Server (Open Source)
  - ServiceId: "grafana-mcp"
    Configuration:
      Type: "MCP"
      MCPServerEndpoint: "http://grafana-mcp-server:8080"
      Authentication:
        Type: "APIKey"
        APIKey: "${GRAFANA_API_KEY}"  # Self-hosted Grafana

# Mock Webhook Configuration
MockWebhookConfiguration:
  # Mock ServiceNow (Lambda Function)
  MockServiceNow:
    LambdaFunction: "mock-servicenow-webhook"
    WebhookURL: "https://api.gateway.url/mock-servicenow"
    TestPayloads:
      - Priority: "High"
        Category: "Application"
        Description: "MSA Service Failure"
  
  # Mock PagerDuty (API Gateway + Lambda)
  MockPagerDuty:
    LambdaFunction: "mock-pagerduty-webhook"
    WebhookURL: "https://api.gateway.url/mock-pagerduty"
    TestPayloads:
      - Urgency: "high"
        Service: "MSA Test Application"
        Description: "Circuit Breaker Failure"

# Mock Slack Integration (WebSocket API)
MockSlackConfiguration:
  WebSocketAPI: "wss://websocket-api.execute-api.us-east-1.amazonaws.com/dev"
  MockChannels:
    - "#devops-alerts-mock"
    - "#msa-incidents-mock"
  SimulatedFeatures:
    - ChannelCreation: true
    - MessagePosting: true
    - UserInteraction: true
```

### Realistic Test Application Architecture

**Infrastructure Stack (AWS Free Tier Compatible):**
```
AWS Free Tier Resources:
├── ECS Fargate (Free tier: 20GB-hours per month)
├── RDS PostgreSQL (Free tier: db.t3.micro, 20GB)
├── API Gateway (Free tier: 1M requests/month)
├── Lambda Functions (Free tier: 1M requests/month)
├── CloudWatch (Free tier: 10 metrics, 5GB logs)
├── X-Ray (Free tier: 100K traces/month)
├── S3 (Free tier: 5GB storage)
└── Application Load Balancer (Not free, but minimal cost)

Open Source Components:
├── Prometheus (Self-hosted on ECS)
├── Grafana (Self-hosted on ECS)
├── Jaeger (Alternative to X-Ray)
└── Custom MCP Servers (Lambda functions)
```

### Revised Repository Structure

**Realistic Source Structure:**
```
msa-devops-agent-test/
├── infrastructure/
│   ├── cloudformation/
│   │   ├── vpc-and-networking.yml
│   │   ├── ecs-cluster.yml
│   │   ├── rds-database.yml
│   │   ├── api-gateway.yml
│   │   ├── monitoring-stack.yml      # CloudWatch, X-Ray
│   │   └── mock-services.yml         # Mock ServiceNow, PagerDuty, Slack
│   └── terraform/ (alternative)
├── services/
│   ├── user-service/                 # Node.js + Express
│   │   ├── src/
│   │   ├── Dockerfile
│   │   ├── package.json
│   │   └── cloudwatch-config.json
│   ├── order-service/                # Python + FastAPI
│   ├── payment-service/              # Java + Spring Boot
│   └── notification-service/         # AWS Lambda (Node.js)
├── frontend/
│   ├── react-app/                    # Hosted on S3 + CloudFront
│   └── build-and-deploy.sh
├── monitoring/
│   ├── prometheus/
│   │   ├── prometheus.yml
│   │   ├── Dockerfile
│   │   └── mcp-server/               # Custom MCP server for Prometheus
│   ├── grafana/
│   │   ├── dashboards/
│   │   ├── Dockerfile
│   │   └── mcp-server/               # Custom MCP server for Grafana
│   └── jaeger/                       # Alternative tracing
├── mock-services/
│   ├── mock-servicenow/
│   │   ├── lambda-function.py        # Simulate ServiceNow webhooks
│   │   └── api-gateway-config.yml
│   ├── mock-pagerduty/
│   │   ├── lambda-function.py        # Simulate PagerDuty webhooks
│   │   └── api-gateway-config.yml
│   └── mock-slack/
│       ├── websocket-api.py          # Simulate Slack interactions
│       └── lambda-authorizer.py
├── problem-simulator/
│   ├── aws-native-problems/
│   │   ├── ecs-task-failure.py       # Stop ECS tasks
│   │   ├── rds-connection-limit.py   # Max out RDS connections
│   │   ├── api-gateway-throttle.py   # Trigger API throttling
│   │   └── lambda-timeout.py         # Force Lambda timeouts
│   ├── msa-problems/
│   │   ├── circuit-breaker-sim.py    # Simulate via env variables
│   │   ├── service-discovery-fail.py # ECS service registration issues
│   │   └── distributed-trace-loss.py # X-Ray sampling issues
│   └── infrastructure-problems/
│       ├── cpu-memory-spike.py       # ECS task resource limits
│       └── network-latency.py        # Security group modifications
├── test-controller/
│   ├── src/
│   │   ├── aws-devops-agent-client.py # DevOps Agent API client
│   │   ├── scenario-executor.py
│   │   ├── validation-framework.py
│   │   └── report-generator.py
│   └── scenarios/
│       ├── aws-native-scenarios.json
│       └── msa-failure-scenarios.json
└── .github/
    └── workflows/
        ├── deploy-infrastructure.yml
        ├── deploy-services.yml
        └── run-devops-agent-tests.yml
```

### Implementation Requirements Analysis

**What We Need to Build:**

1. **Custom MCP Servers** (Essential):
```python
# Prometheus MCP Server
class PrometheusMCPServer:
    def __init__(self, prometheus_url):
        self.prometheus_url = prometheus_url
    
    def query_metrics(self, query, time_range):
        # Query Prometheus API
        # Return standardized metrics format
        pass
    
    def get_service_health(self, service_name):
        # Query service-specific metrics
        pass

# Grafana MCP Server  
class GrafanaMCPServer:
    def __init__(self, grafana_url, api_key):
        self.grafana_url = grafana_url
        self.api_key = api_key
    
    def get_dashboard_data(self, dashboard_id):
        # Fetch dashboard data via Grafana API
        pass
    
    def query_annotations(self, time_range):
        # Get deployment annotations
        pass
```

2. **Mock Service Implementations**:
```python
# Mock ServiceNow Webhook Handler
def mock_servicenow_handler(event, context):
    """Lambda function to simulate ServiceNow webhooks"""
    
    # Parse incoming incident
    incident = json.loads(event['body'])
    
    # Trigger DevOps Agent investigation
    devops_agent_webhook_url = os.environ['DEVOPS_AGENT_WEBHOOK_URL']
    
    payload = {
        "source": "servicenow",
        "incident_id": incident['incident_id'],
        "priority": incident['priority'],
        "description": incident['description']
    }
    
    response = requests.post(devops_agent_webhook_url, json=payload)
    
    return {
        'statusCode': 200,
        'body': json.dumps({'status': 'webhook_sent'})
    }
```

3. **AWS-Native Problem Simulation**:
```python
class AWSNativeProblemSimulator:
    def __init__(self):
        self.ecs_client = boto3.client('ecs')
        self.rds_client = boto3.client('rds')
        self.apigateway_client = boto3.client('apigateway')
    
    def simulate_ecs_service_failure(self, cluster_name, service_name):
        """Stop ECS tasks to simulate service failure"""
        
        # Get running tasks
        tasks = self.ecs_client.list_tasks(
            cluster=cluster_name,
            serviceName=service_name
        )
        
        # Stop tasks to trigger failure
        for task_arn in tasks['taskArns']:
            self.ecs_client.stop_task(
                cluster=cluster_name,
                task=task_arn,
                reason='DevOps Agent Test - Simulated Failure'
            )
    
    def simulate_rds_connection_exhaustion(self, db_instance_id):
        """Create many connections to exhaust RDS connection pool"""
        
        # Get RDS endpoint
        db_info = self.rds_client.describe_db_instances(
            DBInstanceIdentifier=db_instance_id
        )
        
        endpoint = db_info['DBInstances'][0]['Endpoint']['Address']
        
        # Create multiple connections (up to max_connections)
        connections = []
        try:
            for i in range(100):  # Adjust based on RDS instance size
                conn = psycopg2.connect(
                    host=endpoint,
                    database='testdb',
                    user='testuser',
                    password=os.environ['DB_PASSWORD']
                )
                connections.append(conn)
        except Exception as e:
            print(f"Connection exhaustion achieved: {e}")
        
        return connections  # Keep connections open to maintain exhaustion
```

**Cost Considerations:**
- ECS Fargate: ~$20-30/month for test services
- RDS db.t3.micro: Free tier (first 12 months)
- API Gateway: Free tier covers testing needs
- Lambda: Free tier covers testing needs
- CloudWatch: Minimal cost for test logs/metrics
- **Total estimated cost: $20-40/month**

**Integration Complexity:**
- **Low**: AWS native services (CloudWatch, X-Ray)
- **Medium**: Open source tools (Prometheus, Grafana) via MCP servers
- **High**: Mock services for external integrations

이렇게 현실적인 구성으로 수정하면 실제로 구현 가능하고 비용 효율적인 테스트 환경을 만들 수 있습니다.

### Test Application Source Structure

**Repository Structure:**
```
msa-test-application/
├── infrastructure/
│   ├── terraform/
│   │   ├── main.tf                 # Main infrastructure
│   │   ├── ecs-cluster.tf         # ECS cluster setup
│   │   ├── rds.tf                 # Database setup
│   │   ├── api-gateway.tf         # API Gateway config
│   │   └── monitoring.tf          # CloudWatch setup
│   └── cloudformation/
│       └── devops-agent-test.yml  # Existing CFN template
├── services/
│   ├── user-service/
│   │   ├── src/
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml
│   │   └── k8s/
│   ├── order-service/
│   ├── payment-service/
│   └── notification-service/
├── frontend/
│   ├── react-app/
│   └── deployment/
├── problem-simulator/
│   ├── chaos-engineering/
│   │   ├── cpu-spike.py
│   │   ├── memory-leak.py
│   │   ├── network-partition.py
│   │   └── circuit-breaker-failure.py
│   ├── msa-problems/
│   │   ├── service-mesh-failure.py
│   │   ├── dependency-chain-break.py
│   │   └── distributed-tracing-loss.py
│   └── deployment-problems/
├── test-controller/
│   ├── src/
│   │   ├── scenario-manager.ts
│   │   ├── agent-space-manager.ts
│   │   ├── validation-framework.ts
│   │   └── reporting-engine.ts
│   └── scenarios/
│       ├── basic-error-scenarios.json
│       ├── msa-failure-scenarios.json
│       └── complex-incident-scenarios.json
└── monitoring-setup/
    ├── datadog/
    ├── newrelic/
    └── grafana/
```

### Testing Methodology

#### 1. Agent Space Testing

**Setup Phase:**
```python
# Agent Space Creation Test
def test_agent_space_creation():
    """Test Agent Space creation with proper IAM roles"""
    
    # Create Agent Space via AWS DevOps Agent API
    agent_space_config = {
        "name": "test-msa-environment",
        "description": "Test environment for MSA validation",
        "aws_configuration": {
            "account_id": "123456789012",
            "assumable_role_arn": "arn:aws:iam::123456789012:role/DevOpsAgentTestRole",
            "regions": ["us-east-1"]
        }
    }
    
    response = devops_agent_client.create_agent_space(agent_space_config)
    assert response.status_code == 201
    
    agent_space_id = response.json()["agent_space_id"]
    
    # Verify IAM role creation
    iam_client = boto3.client('iam')
    role = iam_client.get_role(RoleName='DevOpsAgentTestRole')
    assert role is not None
    
    return agent_space_id

# Service Association Test
def test_service_associations():
    """Test integration with observability tools"""
    
    # Associate Datadog MCP Server
    datadog_config = {
        "service_id": "datadog-mcp",
        "configuration": {
            "type": "MCP",
            "mcp_server_endpoint": "https://mcp.datadoghq.com",
            "authentication": {
                "type": "OAuth2",
                "client_id": os.getenv("DATADOG_CLIENT_ID")
            }
        }
    }
    
    response = devops_agent_client.associate_service(
        agent_space_id, datadog_config
    )
    assert response.status_code == 201
    
    # Verify connection
    association = response.json()["association"]
    assert association["status"] == "VALID"
```

#### 2. Problem Simulation Testing

**MSA Problem Generation:**
```python
class MSAProblemSimulator:
    def __init__(self, ecs_cluster_name, service_names):
        self.ecs_client = boto3.client('ecs')
        self.cluster_name = ecs_cluster_name
        self.service_names = service_names
    
    def simulate_circuit_breaker_failure(self, service_name):
        """Simulate circuit breaker malfunction"""
        
        # Update service with problematic task definition
        task_def = self._get_task_definition(service_name)
        
        # Inject circuit breaker failure environment variable
        task_def['containerDefinitions'][0]['environment'].append({
            'name': 'CIRCUIT_BREAKER_FORCE_OPEN',
            'value': 'true'
        })
        
        # Register new task definition
        new_task_def = self.ecs_client.register_task_definition(**task_def)
        
        # Update service to use new task definition
        self.ecs_client.update_service(
            cluster=self.cluster_name,
            service=service_name,
            taskDefinition=new_task_def['taskDefinition']['taskDefinitionArn']
        )
        
        return {
            'problem_type': 'circuit_breaker_failure',
            'affected_service': service_name,
            'timestamp': datetime.utcnow().isoformat()
        }
    
    def simulate_service_mesh_partition(self, services):
        """Simulate service mesh network partition"""
        
        # Update Envoy proxy configuration to block inter-service communication
        for service in services:
            # Inject network partition via sidecar configuration
            self._update_envoy_config(service, block_outbound=True)
        
        return {
            'problem_type': 'service_mesh_partition',
            'affected_services': services,
            'timestamp': datetime.utcnow().isoformat()
        }
```

#### 3. End-to-End Testing Workflow

**Automated Test Execution:**
```python
class DevOpsAgentE2ETest:
    def __init__(self):
        self.test_controller = TestController()
        self.problem_simulator = MSAProblemSimulator()
        self.validation_framework = ValidationFramework()
    
    def execute_test_scenario(self, scenario_name):
        """Execute complete test scenario"""
        
        # 1. Setup test environment
        agent_space_id = self.test_controller.create_agent_space()
        self.test_controller.configure_integrations(agent_space_id)
        
        # 2. Deploy test application
        self.test_controller.deploy_msa_application()
        
        # 3. Wait for topology discovery
        time.sleep(300)  # 5 minutes for Agent to build topology
        
        # 4. Simulate problem
        problem_details = self.problem_simulator.simulate_problem(scenario_name)
        
        # 5. Trigger investigation (via webhook or manual)
        if scenario_name.startswith('webhook_'):
            investigation_id = self._trigger_via_webhook(problem_details)
        else:
            investigation_id = self._trigger_manual_investigation(problem_details)
        
        # 6. Monitor investigation progress
        investigation_result = self._wait_for_investigation_completion(investigation_id)
        
        # 7. Validate results
        validation_result = self.validation_framework.validate_investigation(
            investigation_result, problem_details
        )
        
        # 8. Generate report
        report = self.validation_framework.generate_report(
            scenario_name, validation_result
        )
        
        # 9. Cleanup
        self.test_controller.cleanup_environment(agent_space_id)
        
        return report
    
    def _trigger_via_webhook(self, problem_details):
        """Trigger investigation via ServiceNow webhook"""
        
        webhook_payload = {
            "incident_id": f"INC{random.randint(100000, 999999)}",
            "priority": "High",
            "category": "Application",
            "description": f"MSA Application Issue: {problem_details['problem_type']}",
            "assignment_group": "DevOps Team",
            "affected_service": problem_details.get('affected_service', 'Unknown')
        }
        
        # Send webhook to DevOps Agent
        webhook_url = f"https://devops-agent.amazonaws.com/v1/agentspaces/{self.agent_space_id}/webhooks/servicenow"
        
        response = requests.post(
            webhook_url,
            json=webhook_payload,
            headers={
                'X-ServiceNow-Signature': self._generate_hmac_signature(webhook_payload)
            }
        )
        
        assert response.status_code == 200
        return response.json()["investigation_id"]
```

#### 4. Validation Framework

**Agent Performance Validation:**
```python
class ValidationFramework:
    def validate_investigation(self, investigation_result, expected_problem):
        """Validate DevOps Agent investigation results"""
        
        validation_result = {
            'detection': self._validate_detection(investigation_result),
            'root_cause': self._validate_root_cause(investigation_result, expected_problem),
            'mitigation': self._validate_mitigation(investigation_result),
            'collaboration': self._validate_collaboration(investigation_result)
        }
        
        # Calculate overall score
        scores = [v['score'] for v in validation_result.values()]
        validation_result['overall_score'] = sum(scores) / len(scores)
        validation_result['passed'] = validation_result['overall_score'] >= 70
        
        return validation_result
    
    def _validate_root_cause(self, investigation_result, expected_problem):
        """Validate root cause identification accuracy"""
        
        identified_causes = investigation_result.get('root_causes', [])
        expected_cause = expected_problem['problem_type']
        
        # Check if correct root cause was identified
        correct_cause_found = any(
            expected_cause.lower() in cause.lower() 
            for cause in identified_causes
        )
        
        # Check confidence level
        confidence = investigation_result.get('confidence', 0)
        
        # Check relevant factors
        topology_analyzed = investigation_result.get('topology_analysis', {})
        deployment_history_checked = topology_analyzed.get('deployment_history_analyzed', False)
        
        score = 0
        if correct_cause_found:
            score += 40
        if confidence >= 0.8:
            score += 30
        if deployment_history_checked:
            score += 20
        if len(identified_causes) <= 3:  # Focused analysis
            score += 10
        
        return {
            'correct_root_cause': correct_cause_found,
            'confidence': confidence,
            'relevant_factors_identified': identified_causes,
            'score': score
        }
```

### Continuous Testing Pipeline

**GitHub Actions Workflow:**
```yaml
name: DevOps Agent Test Suite

on:
  schedule:
    - cron: '0 2 * * *'  # Daily at 2 AM
  workflow_dispatch:

jobs:
  devops-agent-tests:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Setup Test Environment
      run: |
        # Deploy test infrastructure
        cd infrastructure/terraform
        terraform init
        terraform apply -auto-approve
        
        # Wait for resources to be ready
        sleep 300
    
    - name: Run Agent Space Tests
      run: |
        python -m pytest tests/test_agent_space.py -v
    
    - name: Run MSA Problem Simulation Tests
      run: |
        python -m pytest tests/test_msa_problems.py -v
    
    - name: Run End-to-End Scenarios
      run: |
        python test_controller/run_e2e_tests.py \
          --scenarios basic-error,msa-failure,complex-incident
    
    - name: Generate Test Report
      run: |
        python test_controller/generate_report.py \
          --output-format html,json \
          --upload-to-s3
    
    - name: Cleanup Test Environment
      if: always()
      run: |
        cd infrastructure/terraform
        terraform destroy -auto-approve
```

이렇게 구체적인 소스 구성과 테스트 방법론을 통해 AWS DevOps Agent의 실제 기능을 체계적으로 검증할 수 있습니다.