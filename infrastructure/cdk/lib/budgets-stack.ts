import * as cdk from 'aws-cdk-lib';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import { Construct } from 'constructs';

export interface BudgetsStackProps extends cdk.StackProps {
  /**
   * Email address to receive budget alerts.
   * @default - No email notifications (SNS topic only)
   */
  alertEmail?: string;

  /**
   * Monthly budget limit in USD.
   * @default 500
   */
  monthlyBudgetUsd?: number;

  /**
   * Environment name for cost allocation.
   * @default 'development'
   */
  environment?: string;
}

/**
 * Budgets Stack - Cost monitoring and budget alerts
 *
 * Creates:
 * - SNS Topic for budget notifications
 * - Monthly cost budget with 50%, 80%, 100% threshold alerts
 * - Per-service budgets for DynamoDB and Lambda
 * - Cost allocation tags for expense tracking
 */
export class BudgetsStack extends cdk.Stack {
  public readonly alertTopic: sns.ITopic;

  constructor(scope: Construct, id: string, props?: BudgetsStackProps) {
    super(scope, id, props);

    const monthlyBudgetUsd = props?.monthlyBudgetUsd ?? 500;
    const environment = props?.environment ?? 'development';

    // SNS Topic for budget alerts
    this.alertTopic = new sns.Topic(this, 'BudgetAlertTopic', {
      topicName: 'behavior-analyzer-budget-alerts',
      displayName: 'Behavior Analyzer Budget Alerts',
    });

    // Add email subscription if provided
    if (props?.alertEmail) {
      this.alertTopic.addSubscription(
        new subscriptions.EmailSubscription(props.alertEmail)
      );
    }

    // Monthly cost budget with multiple thresholds
    new budgets.CfnBudget(this, 'MonthlyCostBudget', {
      budget: {
        budgetName: 'BehaviorAnalyzer-Monthly-Total',
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: {
          amount: monthlyBudgetUsd,
          unit: 'USD',
        },
        costFilters: {
          TagKeyValue: [`user:Project$BehaviorAnalyzer`],
        },
      },
      notificationsWithSubscribers: [
        // 50% threshold - informational
        {
          notification: {
            notificationType: 'ACTUAL',
            comparisonOperator: 'GREATER_THAN',
            threshold: 50,
            thresholdType: 'PERCENTAGE',
          },
          subscribers: [
            {
              subscriptionType: 'SNS',
              address: this.alertTopic.topicArn,
            },
          ],
        },
        // 80% threshold - warning
        {
          notification: {
            notificationType: 'ACTUAL',
            comparisonOperator: 'GREATER_THAN',
            threshold: 80,
            thresholdType: 'PERCENTAGE',
          },
          subscribers: [
            {
              subscriptionType: 'SNS',
              address: this.alertTopic.topicArn,
            },
          ],
        },
        // 100% threshold - critical
        {
          notification: {
            notificationType: 'ACTUAL',
            comparisonOperator: 'GREATER_THAN',
            threshold: 100,
            thresholdType: 'PERCENTAGE',
          },
          subscribers: [
            {
              subscriptionType: 'SNS',
              address: this.alertTopic.topicArn,
            },
          ],
        },
        // Forecasted 100% - early warning
        {
          notification: {
            notificationType: 'FORECASTED',
            comparisonOperator: 'GREATER_THAN',
            threshold: 100,
            thresholdType: 'PERCENTAGE',
          },
          subscribers: [
            {
              subscriptionType: 'SNS',
              address: this.alertTopic.topicArn,
            },
          ],
        },
      ],
    });

    // Per-service budgets for cost attribution
    // Note: Project uses DynamoDB + Lambda serverless architecture
    const serviceBudgets = [
      { name: 'DynamoDB', service: 'Amazon DynamoDB', limit: monthlyBudgetUsd * 0.6 },
      { name: 'Lambda', service: 'AWS Lambda', limit: monthlyBudgetUsd * 0.3 },
      { name: 'APIGateway', service: 'Amazon API Gateway', limit: monthlyBudgetUsd * 0.1 },
    ];

    for (const svc of serviceBudgets) {
      new budgets.CfnBudget(this, `${svc.name}Budget`, {
        budget: {
          budgetName: `BehaviorAnalyzer-${svc.name}`,
          budgetType: 'COST',
          timeUnit: 'MONTHLY',
          budgetLimit: {
            amount: svc.limit,
            unit: 'USD',
          },
          costFilters: {
            Service: [svc.service],
          },
        },
        notificationsWithSubscribers: [
          {
            notification: {
              notificationType: 'ACTUAL',
              comparisonOperator: 'GREATER_THAN',
              threshold: 80,
              thresholdType: 'PERCENTAGE',
            },
            subscribers: [
              {
                subscriptionType: 'SNS',
                address: this.alertTopic.topicArn,
              },
            ],
          },
          {
            notification: {
              notificationType: 'ACTUAL',
              comparisonOperator: 'GREATER_THAN',
              threshold: 100,
              thresholdType: 'PERCENTAGE',
            },
            subscribers: [
              {
                subscriptionType: 'SNS',
                address: this.alertTopic.topicArn,
              },
            ],
          },
        ],
      });
    }

    // Cost anomaly detection budget (uses ML to detect unusual spending)
    new budgets.CfnBudget(this, 'AnomalyDetectionBudget', {
      budget: {
        budgetName: 'BehaviorAnalyzer-Anomaly-Detection',
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: {
          amount: monthlyBudgetUsd * 1.2, // 20% buffer for anomaly detection
          unit: 'USD',
        },
        costFilters: {
          TagKeyValue: [`user:Project$BehaviorAnalyzer`],
        },
      },
      notificationsWithSubscribers: [
        {
          notification: {
            notificationType: 'ACTUAL',
            comparisonOperator: 'GREATER_THAN',
            threshold: 100,
            thresholdType: 'PERCENTAGE',
          },
          subscribers: [
            {
              subscriptionType: 'SNS',
              address: this.alertTopic.topicArn,
            },
          ],
        },
      ],
    });

    // Outputs
    new cdk.CfnOutput(this, 'AlertTopicArn', {
      value: this.alertTopic.topicArn,
      description: 'SNS Topic ARN for budget alerts',
      exportName: 'BehaviorAnalyzerBudgetAlertTopicArn',
    });

    new cdk.CfnOutput(this, 'MonthlyBudgetLimit', {
      value: `$${monthlyBudgetUsd} USD`,
      description: 'Monthly budget limit',
    });

    // Apply cost allocation tags to all resources in this stack
    cdk.Tags.of(this).add('Project', 'BehaviorAnalyzer');
    cdk.Tags.of(this).add('Environment', environment);
    cdk.Tags.of(this).add('CostCenter', 'BehaviorAnalyzer');
  }
}
