from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
# Create your views here.
from rest_framework import generics
from .serializers import RegisterSerializer
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema



@extend_schema(
    request=RegisterSerializer,
    
    responses={
        201: OpenApiResponse(
            description="User registered successfully"
        ),
        400: OpenApiResponse(
            description="Registration failed. Validation errors are returned."
        ),
    },
    description="Register a new user",
    summary="User Registration",
    tags=["Authentication"],
)

@api_view(['post'])
def register_user(request):

    serializers = RegisterSerializer(data=request.data)

    if serializers.is_valid():

        serializers.save()

        return Response(
            {
                "message": "User registered successfully",
                "user": serializers.data
            },
            status=status.HTTP_201_CREATED
        )
    
    return Response({"message": "Failed to register user", "errors": serializers.errors}, status=status.HTTP_400_BAD_REQUEST)