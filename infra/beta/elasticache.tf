locals {
  redis_security_group_id = "sg-0a20d8dea7844653c"
}

# ElastiCache Redis cluster - minimal configuration
resource "aws_elasticache_cluster" "agent_leasing" {
  cluster_id           = "beta-agent-leasing"
  engine               = "redis"
  node_type            = "cache.t3.micro"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  engine_version       = "7.1"
  port                 = 6379
  subnet_group_name    = "ccnp-elasticache-subnet-group"
  security_group_ids   = [local.redis_security_group_id]

  # Backup configuration
  snapshot_retention_limit = 5
  snapshot_window          = "03:00-05:00"
  maintenance_window       = "sun:06:00-sun:07:00"

  tags = var.tags
}

# Output the Redis endpoint
output "redis_endpoint" {
  value       = aws_elasticache_cluster.agent_leasing.cache_nodes[0].address
  description = "The endpoint of the Redis cluster"
}

output "redis_connection_string" {
  value       = "redis://${aws_elasticache_cluster.agent_leasing.cache_nodes[0].address}:${aws_elasticache_cluster.agent_leasing.port}"
  description = "The full Redis connection string"
}
