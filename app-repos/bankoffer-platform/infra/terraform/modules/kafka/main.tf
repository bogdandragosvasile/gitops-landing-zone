# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------
variable "environment" {
  type = string
}

variable "cluster_name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "kafka_version" {
  type = string
}

variable "broker_instance_type" {
  type = string
}

variable "number_of_brokers" {
  type = number
}

variable "ebs_volume_size" {
  type = number
}

variable "eks_security_group_id" {
  type = string
}

# ---------------------------------------------------------------------------
# Security Group
# ---------------------------------------------------------------------------
resource "aws_security_group" "msk" {
  name_prefix = "${var.cluster_name}-msk-"
  description = "Security group for MSK cluster"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Kafka TLS from EKS"
    from_port       = 9094
    to_port         = 9094
    protocol        = "tcp"
    security_groups = [var.eks_security_group_id]
  }

  ingress {
    description     = "Kafka plaintext from EKS"
    from_port       = 9092
    to_port         = 9092
    protocol        = "tcp"
    security_groups = [var.eks_security_group_id]
  }

  ingress {
    description     = "Zookeeper from EKS"
    from_port       = 2181
    to_port         = 2181
    protocol        = "tcp"
    security_groups = [var.eks_security_group_id]
  }

  ingress {
    description = "Inter-broker communication"
    from_port   = 9094
    to_port     = 9094
    protocol    = "tcp"
    self        = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.cluster_name}-msk-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ---------------------------------------------------------------------------
# CloudWatch Log Group
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "msk" {
  name              = "/aws/msk/${var.cluster_name}-${var.environment}"
  retention_in_days = var.environment == "prod" ? 90 : 14
}

# ---------------------------------------------------------------------------
# MSK Configuration
# ---------------------------------------------------------------------------
resource "aws_msk_configuration" "this" {
  name              = "${var.cluster_name}-${var.environment}-config"
  kafka_versions    = [var.kafka_version]
  description       = "MSK configuration for ${var.cluster_name}"

  server_properties = <<-PROPERTIES
    auto.create.topics.enable=false
    default.replication.factor=3
    min.insync.replicas=2
    num.partitions=6
    num.io.threads=8
    num.network.threads=5
    num.replica.fetchers=2
    socket.request.max.bytes=104857600
    unclean.leader.election.enable=false
    log.retention.hours=168
    log.retention.bytes=-1
  PROPERTIES
}

# ---------------------------------------------------------------------------
# MSK Cluster
# ---------------------------------------------------------------------------
resource "aws_msk_cluster" "this" {
  cluster_name           = "${var.cluster_name}-${var.environment}"
  kafka_version          = var.kafka_version
  number_of_broker_nodes = var.number_of_brokers

  configuration_info {
    arn      = aws_msk_configuration.this.arn
    revision = aws_msk_configuration.this.latest_revision
  }

  broker_node_group_info {
    instance_type  = var.broker_instance_type
    client_subnets = slice(var.private_subnet_ids, 0, var.number_of_brokers)

    storage_info {
      ebs_storage_info {
        volume_size = var.ebs_volume_size
      }
    }

    security_groups = [aws_security_group.msk.id]
  }

  encryption_info {
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = aws_cloudwatch_log_group.msk.name
      }
    }
  }

  open_monitoring {
    prometheus {
      jmx_exporter {
        enabled_in_broker = true
      }
      node_exporter {
        enabled_in_broker = true
      }
    }
  }

  tags = {
    Name        = "${var.cluster_name}-${var.environment}"
    Environment = var.environment
  }
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
output "bootstrap_brokers_tls" {
  value = aws_msk_cluster.this.bootstrap_brokers_tls
}

output "zookeeper_connect" {
  value = aws_msk_cluster.this.zookeeper_connect_string
}

output "cluster_arn" {
  value = aws_msk_cluster.this.arn
}
